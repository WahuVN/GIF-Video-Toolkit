#!/usr/bin/env python3
"""Offline, read-only, fail-closed public-distribution gate for GIF-Video-Toolkit.

The script cannot establish copyright ownership or authenticate an owner's signature.
An --approval-digest is an assertion to CHECK, not permission to release. In particular
this gate never treats imageio-ffmpeg's package metadata as a grant to redistribute an
FFmpeg executable. A manual owner/legal/provenance decision remains required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

SCHEMA = "GHMEDIA125.offline-release-gate.v1"
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md", "COPYING.txt")
NOTICE_NAMES = ("THIRD-PARTY-NOTICES.md", "THIRD_PARTY_NOTICES.md", "NOTICE", "NOTICE.md")
PRIVATE_PARTS = {".env", ".aws", ".ssh", ".wahu-brain", ".wahu-admin", "node_modules",
                 "credentials", "secrets", "private", "session", "sessions", "runtime", "tokens"}
PRIVATE_NAME = re.compile(r"(?:^|[._-])(token|secret|password|cookie|credential|apikey|api_key|session|private_key)(?:[._-]|$)", re.I)
RUNTIME_PARTS = {"__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache", "dist", "build"}
MAX_FILE_BYTES = 8 * 1024 * 1024


def _git(root: Path, *args: str) -> bytes | None:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1")
    try:
        p = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                            "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           env=env, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check(root: Path, approval_digest: str | None = None) -> dict:
    """Return only a fixed-schema redacted report, never raw paths/file contents."""
    reasons: set[str] = set()
    counts = {"candidate_files": 0, "bundled_ffmpeg": 0, "other_executables": 0,
              "private_payloads": 0, "runtime_payloads": 0, "symlinks": 0}
    digest = hashlib.sha256(b"GHMEDIA125-candidate-v1\0")
    file_hashes: dict[str, str] = {}
    safe_readme = b""
    safe_requirements = b""
    safe_notice = b""
    files: list[str] = []

    if root.is_symlink() or not root.is_dir():
        reasons.add("ROOT_UNSAFE_OR_MISSING")
        return _report(reasons, counts, None, approval_digest)
    if (root / ".git").is_symlink():
        reasons.add("SYMLINK_PRESENT")
        counts["symlinks"] += 1
    if not (root / ".git").exists():
        reasons.add("GIT_METADATA_MISSING")
    tracked = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if tracked is None or status is None:
        reasons.add("GIT_INSPECTION_FAILED")
    else:
        if status:
            reasons.add("DIRTY_WORKTREE")
        try:
            files = sorted({part.decode("utf-8", "strict").replace("\\", "/")
                            for part in tracked.split(b"\0") if part})
        except UnicodeError:
            reasons.add("UNSAFE_FILENAME_ENCODING")
        if not files:
            reasons.add("EMPTY_CANDIDATE")

    for name in files:
        parts = name.split("/")
        lower_parts = [p.casefold() for p in parts]
        if (not parts or any(p in ("", ".", "..") for p in parts)
                or name.startswith("/") or ":" in parts[0] or "\x00" in name):
            reasons.add("UNSAFE_CANDIDATE_PATH")
            continue
        if (any(p in PRIVATE_PARTS or p.startswith(".env.") for p in lower_parts)
                or PRIVATE_NAME.search(parts[-1])
                or parts[-1].casefold() in {"id_rsa", "id_ed25519"}
                or parts[-1].casefold().endswith((".pem", ".p12", ".pfx", ".key", ".sqlite", ".sqlite3"))):
            reasons.add("PRIVATE_PAYLOAD_INCLUDED")
            counts["private_payloads"] += 1
            continue  # Do not open or hash private material.
        if any(p in RUNTIME_PARTS for p in lower_parts) or parts[-1].casefold().endswith((".pyc", ".pyo", ".log", ".tmp")):
            reasons.add("RUNTIME_PAYLOAD_INCLUDED")
            counts["runtime_payloads"] += 1
            continue
        current = root
        unsafe = False
        for part in parts:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except OSError:
                reasons.add("CANDIDATE_CHANGED_OR_UNREADABLE")
                unsafe = True
                break
            if stat.S_ISLNK(mode):
                reasons.add("SYMLINK_PRESENT")
                counts["symlinks"] += 1
                unsafe = True
                break
        if unsafe:
            continue
        try:
            st = current.stat()
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
                reasons.add("UNSAFE_FILE_TYPE_OR_SIZE")
                continue
            data = current.read_bytes()
            if len(data) != st.st_size:
                reasons.add("CANDIDATE_CHANGED_OR_UNREADABLE")
                continue
        except OSError:
            reasons.add("CANDIDATE_CHANGED_OR_UNREADABLE")
            continue
        digest.update(name.encode("utf-8") + b"\0" + len(data).to_bytes(8, "big") + hashlib.sha256(data).digest())
        file_hashes[name.casefold()] = _sha256(data)
        counts["candidate_files"] += 1
        lower = parts[-1].casefold()
        if lower in {"ffmpeg.exe", "ffprobe.exe", "ffmpeg", "ffprobe"} or "imageio_ffmpeg/binaries/" in name.casefold():
            counts["bundled_ffmpeg"] += 1
            reasons.add("BUNDLED_FFMPEG_BINARY_REQUIRES_VERIFIED_LICENSE_AND_PROVENANCE")
        elif lower.endswith((".exe", ".dll", ".so", ".dylib", ".msi", ".zip", ".7z")):
            counts["other_executables"] += 1
            reasons.add("OTHER_BINARY_PAYLOAD_REQUIRES_REVIEW")
        if name.casefold() == "readme.md":
            safe_readme = data
        elif name.casefold() == "requirements.txt":
            safe_requirements = data
        elif name.casefold() in {n.casefold() for n in NOTICE_NAMES}:
            safe_notice += b"\n" + data

    if not any(file_hashes.get(n.casefold()) for n in LICENSE_NAMES):
        reasons.add("ROOT_PROJECT_LICENSE_MISSING")
    # Existence of a LICENSE is NOT proof the owner wrote it or grants redistribution.
    reasons.add("OWNER_DISTRIBUTION_AUTHORITY_UNVERIFIED")
    if not safe_readme:
        reasons.add("PROJECT_README_MISSING")
    if not safe_requirements:
        reasons.add("REQUIREMENTS_UNREADABLE_OR_MISSING")
    try:
        requirements = safe_requirements.decode("utf-8-sig", "strict")
    except UnicodeError:
        requirements = ""
        reasons.add("REQUIREMENTS_UNREADABLE_OR_MISSING")
    if re.search(r"(?im)^\s*imageio[-_]ffmpeg\s*(?:[>=<~!]|$)", requirements):
        if not re.search(r"(?im)^\s*imageio[-_]ffmpeg\s*==\s*\d", requirements):
            reasons.add("IMAGEIO_FFMPEG_VERSION_NOT_PINNED")
        notice = safe_notice.decode("utf-8", "replace").casefold()
        if "imageio-ffmpeg" not in notice and "imageio_ffmpeg" not in notice:
            reasons.add("IMAGEIO_FFMPEG_ATTRIBUTION_MISSING")
    if not safe_notice or "ffmpeg" not in safe_notice.decode("utf-8", "replace").casefold():
        reasons.add("FFMPEG_THIRD_PARTY_NOTICE_MISSING")
    # A URL, package requirement, filename or -version stdout cannot independently
    # attest to a binary's original source, configure flags, and license obligations.
    if "third_party/ffmpeg-provenance.json" not in file_hashes:
        reasons.add("FFMPEG_EXECUTABLE_SOURCE_BUILD_CONFIGURATION_UNVERIFIED")
    else:
        # Owner-written manifest alone is not independently authenticated evidence.
        reasons.add("FFMPEG_PROVENANCE_INDEPENDENT_VERIFICATION_REQUIRED")
    if counts["bundled_ffmpeg"]:
        if "third_party/ffmpeg-license.txt" not in file_hashes:
            reasons.add("BUNDLED_FFMPEG_LICENSE_EVIDENCE_MISSING")
        else:
            reasons.add("BUNDLED_FFMPEG_LICENSE_INDEPENDENT_VERIFICATION_REQUIRED")
    if not counts["bundled_ffmpeg"] and tracked is not None:
        # Record absence without claiming an external or pip-installed binary is cleared.
        pass
    candidate_digest = digest.hexdigest() if (tracked is not None and files and
                                            not (reasons & {"UNSAFE_CANDIDATE_PATH", "UNSAFE_FILENAME_ENCODING",
                                                            "CANDIDATE_CHANGED_OR_UNREADABLE", "UNSAFE_FILE_TYPE_OR_SIZE",
                                                            "SYMLINK_PRESENT", "PRIVATE_PAYLOAD_INCLUDED", "RUNTIME_PAYLOAD_INCLUDED"})) else None
    if approval_digest is not None:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", approval_digest) or candidate_digest is None or approval_digest.lower() != candidate_digest:
            reasons.add("OWNER_APPROVAL_DIGEST_MISMATCH_OR_UNVERIFIABLE")
        else:
            reasons.add("OWNER_APPROVAL_UNAUTHENTICATED_NOT_A_LICENSE")
    # There is deliberately NO machine-only release override: independent owner and
    # copyright/provenance verification is not established by local JSON or digest.
    reasons.add("PUBLIC_RELEASE_AUTHORIZATION_REQUIRED")
    return _report(reasons, counts, candidate_digest, approval_digest)


def _report(reasons: set[str], counts: dict, digest: str | None, approval: str | None) -> dict:
    return {"schema": SCHEMA, "decision": "HOLD", "release_authorized": False,
            "candidate_digest_sha256": digest, "approval_digest_supplied": approval is not None,
            "bundled_ffmpeg": "present" if counts["bundled_ffmpeg"] else "not_detected_in_candidate",
            "counts": counts, "reasons": sorted(reasons)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-only, read-only release gate (never publishes).")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--approval-digest", default=None, help="Owner-supplied candidate digest to compare, NOT release authorization")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)
    report = check(args.root, args.approval_digest)
    if args.format == "json":
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    else:
        print("PUBLIC_RELEASE_HOLD")
        print("candidate_digest_sha256=" + (report["candidate_digest_sha256"] or "UNAVAILABLE"))
        print("bundled_ffmpeg=" + report["bundled_ffmpeg"])
        for reason in report["reasons"]:
            print("HOLD_REASON=" + reason)
    return 2


if __name__ == "__main__":
    sys.exit(main())
