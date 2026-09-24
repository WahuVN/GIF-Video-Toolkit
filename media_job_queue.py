from __future__ import annotations

import threading
import traceback
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional

from media_runtime import CancelledError, CancellationToken


@dataclass
class JobSnapshot:
    id: str
    label: str
    status: str
    progress: float = 0.0
    message: str = ""
    error: str = ""


class SerialJobQueue:
    """Sequential background queue with per-job cancellation and observable state."""

    def __init__(self, on_change: Optional[Callable[[JobSnapshot], None]] = None) -> None:
        self._on_change = on_change
        self._items: Deque[tuple[str, str, Callable[[CancellationToken, Callable[[float, str], None]], None]]] = deque()
        self._jobs: Dict[str, JobSnapshot] = {}
        self._tokens: Dict[str, CancellationToken] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="media-job-queue")
        self._thread.start()

    def submit(self, label: str, runner: Callable[[CancellationToken, Callable[[float, str], None]], None]) -> str:
        job_id = uuid.uuid4().hex[:10]
        snap = JobSnapshot(job_id, label, "queued")
        with self._lock:
            self._jobs[job_id] = snap
            self._tokens[job_id] = CancellationToken()
            self._items.append((job_id, label, runner))
        self._emit(snap)
        self._wake.set()
        return job_id

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            snap = self._jobs.get(job_id)
            token = self._tokens.get(job_id)
            if snap is None or token is None or snap.status in {"done", "failed", "cancelled"}:
                return False
            token.cancel()
            if snap.status == "queued":
                snap.status = "cancelled"
                snap.message = "Đã hủy trước khi chạy"
                self._emit(snap)
            self._wake.set()
            return True

    def snapshots(self) -> List[JobSnapshot]:
        with self._lock:
            return [JobSnapshot(**vars(v)) for v in self._jobs.values()]

    def close(self) -> None:
        self._stop.set()
        self._wake.set()

    def _set(self, job_id: str, **changes) -> JobSnapshot:
        with self._lock:
            snap = self._jobs[job_id]
            for key, value in changes.items():
                setattr(snap, key, value)
            copied = JobSnapshot(**vars(snap))
        self._emit(copied)
        return copied

    def _emit(self, snap: JobSnapshot) -> None:
        if self._on_change:
            self._on_change(JobSnapshot(**vars(snap)))

    def _progress(self, job_id: str, fraction: float, message: str) -> None:
        self._set(job_id, progress=max(0.0, min(1.0, fraction)), message=message)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.25)
            self._wake.clear()
            while not self._stop.is_set():
                with self._lock:
                    if not self._items:
                        break
                    job_id, label, runner = self._items.popleft()
                    snap = self._jobs[job_id]
                    token = self._tokens[job_id]
                    if snap.status == "cancelled" or token.cancelled:
                        continue
                self._set(job_id, status="running", progress=0.0, message="Đang chạy")
                try:
                    runner(token, lambda f, m, jid=job_id: self._progress(jid, f, m))
                    token.raise_if_cancelled()
                    self._set(job_id, status="done", progress=1.0, message="Hoàn tất")
                except CancelledError:
                    self._set(job_id, status="cancelled", message="Đã hủy")
                except BaseException:
                    self._set(job_id, status="failed", message="Thất bại", error=traceback.format_exc())
