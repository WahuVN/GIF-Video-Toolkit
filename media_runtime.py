from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional


class CancelledError(RuntimeError):
    """Raised when a media job is cancelled by the user."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancelledError("Đã hủy tác vụ")


_DISCOVERY_CACHE: Dict[tuple[str, Optional[str]], str] = {}


def _verify_executable(path: str, version_arg: str = "-version", timeout: float = 4.0) -> str:
    resolved = str(Path(path).expanduser().resolve()) if Path(path).exists() else path
    proc = subprocess.run(
        [resolved, version_arg], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Không chạy được media tool: {resolved}")
    return resolved


def discover_media_tool(name: str, configured: Optional[str] = None, allow_imageio: bool = False) -> str:
    key = (name, configured)
    cached = _DISCOVERY_CACHE.get(key)
    if cached:
        return cached

    candidates = []
    if configured:
        candidates.append(configured)
    env_name = f"WAHU_{name.upper()}_PATH"
    if os.environ.get(env_name):
        candidates.append(os.environ[env_name])
    which = shutil.which(name)
    if which:
        candidates.append(which)
    if allow_imageio and name.lower() == "ffmpeg":
        try:
            import imageio_ffmpeg  # type: ignore
            candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception:
            pass

    errors = []
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            resolved = _verify_executable(candidate)
            _DISCOVERY_CACHE[key] = resolved
            return resolved
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")

    detail = "; ".join(errors) if errors else "không có ứng viên"
    raise RuntimeError(f"Không tìm thấy {name} hợp lệ ({detail})")


def clear_discovery_cache() -> None:
    _DISCOVERY_CACHE.clear()


def terminate_process(proc: subprocess.Popen, timeout: float = 2.0) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=timeout)
        except Exception:
            pass


def snap_time_to_frame(seconds: float, fps: float) -> float:
    if fps <= 0:
        return max(0.0, seconds)
    frame = round(max(0.0, seconds) * fps)
    return frame / fps


def estimate_stream_copy_bytes(input_size_bytes: int, source_duration: float, clip_duration: float) -> int:
    if input_size_bytes <= 0 or source_duration <= 0 or clip_duration <= 0:
        return 0
    ratio = min(1.0, clip_duration / source_duration)
    return max(1, int(round(input_size_bytes * ratio)))


def estimate_precise_bytes(clip_duration: float, crf: int = 20, keep_audio: bool = True,
                           width: int = 1920, height: int = 1080, fps: float = 30.0) -> int:
    if clip_duration <= 0:
        return 0
    pixels = max(1, width * height)
    fps_factor = max(0.25, min(4.0, fps / 30.0))
    resolution_factor = pixels / (1920 * 1080)
    crf_factor = 2 ** ((23 - max(0, min(51, crf))) / 6.0)
    video_bps = 3_000_000 * resolution_factor * fps_factor * crf_factor
    audio_bps = 192_000 if keep_audio else 0
    return int((video_bps + audio_bps) * clip_duration / 8)


def estimate_gif_bytes(width: int, height: int, frames: int, colors: int, palette_mode: str = "per_frame") -> int:
    if width <= 0 or height <= 0 or frames <= 0:
        return 0
    color_factor = max(0.08, min(1.0, colors / 256.0))
    palette_factor = 0.82 if palette_mode == "global" else 1.0
    # Empirical planning estimate only; final GIF compression remains content-dependent.
    bytes_per_pixel_frame = 0.18 * color_factor * palette_factor
    return max(1024, int(width * height * frames * bytes_per_pixel_frame))
