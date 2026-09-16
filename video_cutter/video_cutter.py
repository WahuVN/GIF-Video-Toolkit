#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Công cụ cắt video.

Cốt lõi:
- `cut_video(...)`: hàm thư viện gọi ffmpeg để cắt một đoạn video.
- CLI: `python video_cutter.py -i in.mp4 --start 10 --end 25 -o out.mp4`
- GUI: `python video_cutter_gui.py` hoặc `python video_cutter.py --gui`

Hai chế độ cắt:
- `copy` (mặc định): siêu nhanh, không tái mã hóa, không mất chất lượng.
  Có thể lệch một chút so với mốc thời gian yêu cầu vì phải bám keyframe.
- `precise`: tái mã hóa H.264 + AAC. Cắt chuẩn từng frame, cũng dùng để
  ép vào kích thước/tốc độ cụ thể nếu cần.

ffmpeg được lấy từ gói `imageio-ffmpeg` (binary đi kèm), không cần cài hệ thống.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence


ProgressCallback = Callable[[float, str], None]
"""(fraction_0_to_1, message) -> None"""


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe discovery
# ---------------------------------------------------------------------------


def _ffmpeg_exe() -> str:
    """Trả về đường dẫn ffmpeg. Ưu tiên ffmpeg trên PATH, fallback imageio-ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "Không tìm thấy ffmpeg. Cài gói: pip install imageio-ffmpeg"
        ) from exc


def _ffprobe_exe() -> Optional[str]:
    """Trả về đường dẫn ffprobe nếu có, ngược lại None (sẽ fallback dùng ffmpeg)."""
    return shutil.which("ffprobe")


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------


def parse_timecode(value: str) -> float:
    """Hỗ trợ '12.5', '01:23', '01:02:03', '01:02:03.4'."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Thời gian rỗng")
    if ":" not in value:
        seconds = float(value)
        if seconds < 0:
            raise ValueError("Thời gian phải >= 0")
        return seconds

    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Định dạng thời gian không hợp lệ: {value!r}")
    nums = [float(p) for p in parts]
    if any(n < 0 for n in nums):
        raise ValueError("Thành phần thời gian phải >= 0")
    if len(nums) == 2:
        m, s = nums
        return m * 60 + s
    h, m, s = nums
    return h * 3600 + m * 60 + s


def format_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_ms //= 1000
    s = total_ms % 60
    m = (total_ms // 60) % 60
    h = total_ms // 3600
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m:02d}:{s:02d}.{ms:03d}"


# ---------------------------------------------------------------------------
# Probe input
# ---------------------------------------------------------------------------


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    audio_codec: Optional[str]


def probe(path: Path) -> VideoInfo:
    """Đọc metadata video. Ưu tiên ffprobe, fallback parse từ ffmpeg -i."""
    ffprobe = _ffprobe_exe()
    if ffprobe:
        return _probe_ffprobe(ffprobe, path)
    return _probe_ffmpeg(_ffmpeg_exe(), path)


def _probe_ffprobe(ffprobe: str, path: Path) -> VideoInfo:
    cmd = [
        ffprobe, "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    data = json.loads(out)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if v is None:
        raise RuntimeError(f"Không có luồng video trong: {path}")

    duration = float(fmt.get("duration") or v.get("duration") or 0.0)
    width = int(v.get("width") or 0)
    height = int(v.get("height") or 0)
    fps = _parse_rational(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1")
    codec = v.get("codec_name") or "unknown"
    a_codec = a.get("codec_name") if a else None
    return VideoInfo(duration=duration, width=width, height=height, fps=fps, codec=codec, audio_codec=a_codec)


def _probe_ffmpeg(ffmpeg: str, path: Path) -> VideoInfo:
    """Parse output của 'ffmpeg -i ... -hide_banner' để rút metadata."""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    text = (proc.stderr or "") + (proc.stdout or "")

    duration = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    width = height = 0
    fps = 0.0
    codec = "unknown"
    vmatch = re.search(
        r"Stream #\d+:\d+.*Video:\s*([^,\s]+).*?(\d{2,5})x(\d{2,5})",
        text, re.S,
    )
    if vmatch:
        codec = vmatch.group(1)
        width = int(vmatch.group(2))
        height = int(vmatch.group(3))
    fpsm = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
    if fpsm:
        try:
            fps = float(fpsm.group(1))
        except ValueError:
            fps = 0.0

    audio_codec: Optional[str] = None
    am = re.search(r"Stream #\d+:\d+.*Audio:\s*([^,\s]+)", text)
    if am:
        audio_codec = am.group(1)

    return VideoInfo(
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        codec=codec,
        audio_codec=audio_codec,
    )


def _parse_rational(s: str) -> float:
    if not s or s == "0/0":
        return 0.0
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            num, den = float(a), float(b)
            if den == 0:
                return 0.0
            return num / den
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Cutting
# ---------------------------------------------------------------------------


@dataclass
class CutOptions:
    input_path: Path
    output_path: Path
    start: float
    end: float
    mode: str = "copy"  # "copy" hoặc "precise"
    keep_audio: bool = True
    crf: int = 20
    preset: str = "medium"
    overwrite: bool = True


def _validate(opts: CutOptions) -> None:
    if not opts.input_path.exists():
        raise ValueError(f"Không tìm thấy tệp: {opts.input_path}")
    if opts.end <= opts.start:
        raise ValueError("Thời điểm kết thúc phải lớn hơn bắt đầu")
    if opts.start < 0:
        raise ValueError("Thời điểm bắt đầu phải >= 0")
    if opts.mode not in ("copy", "precise"):
        raise ValueError(f"Chế độ cắt không hợp lệ: {opts.mode!r}")
    if opts.crf < 0 or opts.crf > 51:
        raise ValueError("CRF phải nằm trong khoảng 0..51")


def _build_cmd(opts: CutOptions, ffmpeg: str) -> List[str]:
    """Dựng câu lệnh ffmpeg.

    - mode 'copy': đặt -ss SAU -i để cắt chính xác hơn (vẫn bị giới hạn
      bởi keyframe vì không re-encode), thêm -avoid_negative_ts.
    - mode 'precise': đặt -ss TRƯỚC -i để seek nhanh, rồi re-encode
      H.264 + AAC để bám frame chuẩn.
    """
    duration = opts.end - opts.start
    cmd: List[str] = [ffmpeg, "-hide_banner", "-loglevel", "error", "-stats"]
    cmd.append("-y" if opts.overwrite else "-n")

    if opts.mode == "precise":
        cmd += ["-ss", f"{opts.start:.3f}", "-i", str(opts.input_path), "-t", f"{duration:.3f}"]
        cmd += ["-c:v", "libx264", "-crf", str(opts.crf), "-preset", opts.preset, "-pix_fmt", "yuv420p"]
        if opts.keep_audio:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart"]
    else:
        cmd += ["-i", str(opts.input_path), "-ss", f"{opts.start:.3f}", "-t", f"{duration:.3f}"]
        cmd += ["-c:v", "copy"]
        if opts.keep_audio:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-an"]
        cmd += ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart"]

    cmd.append(str(opts.output_path))
    return cmd


_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def cut_video(opts: CutOptions, on_progress: Optional[ProgressCallback] = None) -> Path:
    _validate(opts)
    opts.output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = _ffmpeg_exe()
    cmd = _build_cmd(opts, ffmpeg)

    duration = max(1e-3, opts.end - opts.start)
    if on_progress:
        on_progress(0.0, "Bắt đầu cắt video…")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    last_lines: List[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        last_lines.append(line.rstrip())
        if len(last_lines) > 80:
            del last_lines[0]
        m = _TIME_RE.search(line)
        if m and on_progress:
            h, mm, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            elapsed = h * 3600 + mm * 60 + s
            frac = max(0.0, min(0.99, elapsed / duration))
            on_progress(frac, f"Đã xử lý {format_time(elapsed)} / {format_time(duration)}")

    proc.wait()
    if proc.returncode != 0:
        tail = "\n".join(last_lines[-20:])
        raise RuntimeError(f"ffmpeg lỗi (mã {proc.returncode}):\n{tail}")

    if on_progress:
        on_progress(1.0, "Hoàn tất")
    return opts.output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_cutter.py",
        description="Cắt video bằng ffmpeg (chế độ stream-copy nhanh hoặc precise).",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("-i", "--input", help="Tệp video đầu vào")
    parser.add_argument("-o", "--output", help="Tệp video đầu ra")
    parser.add_argument("--start", default="0", help="Thời điểm bắt đầu (giây hoặc HH:MM:SS)")
    parser.add_argument("--end", default=None, help="Thời điểm kết thúc")
    parser.add_argument("--duration", default=None, help="Thời lượng (không dùng cùng --end)")
    parser.add_argument("--mode", choices=["copy", "precise"], default="copy",
                        help="copy = nhanh, không re-encode; precise = chính xác từng frame")
    parser.add_argument("--no-audio", action="store_true", help="Bỏ tiếng")
    parser.add_argument("--crf", type=int, default=20,
                        help="Chất lượng khi precise (0=lossless, 23=mặc định x264, 51=tệ nhất)")
    parser.add_argument("--preset", default="medium",
                        choices=["ultrafast", "superfast", "veryfast", "faster", "fast",
                                 "medium", "slow", "slower", "veryslow"],
                        help="Tốc độ encode khi precise")
    parser.add_argument("--no-overwrite", action="store_true", help="Không ghi đè nếu trùng tên")
    parser.add_argument("--gui", action="store_true", help="Mở giao diện đồ họa")
    parser.add_argument("--probe", action="store_true", help="Chỉ in metadata rồi thoát")
    return parser


def _default_output(input_path: Path, start: float, end: float) -> Path:
    name = f"{input_path.stem}_cut_{int(round(start))}-{int(round(end))}{input_path.suffix or '.mp4'}"
    return (input_path.parent / name).resolve()


def _cli_progress(fraction: float, message: str) -> None:
    pct = int(fraction * 100)
    sys.stdout.write(f"\r[{pct:3d}%] {message[:60]:<60}")
    sys.stdout.flush()
    if pct >= 100:
        sys.stdout.write("\n")
        sys.stdout.flush()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gui:
        try:
            from video_cutter_gui import launch  # type: ignore
        except ImportError as exc:
            raise SystemExit(f"GUI không khả dụng: {exc}")
        launch()
        return 0

    if not args.input:
        raise SystemExit("Lỗi: cần --input. Ví dụ: python video_cutter.py -i in.mp4 --end 10")

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Không tìm thấy tệp: {input_path}")

    if args.probe:
        info = probe(input_path)
        print(f"Tệp        : {input_path}")
        print(f"Thời lượng : {format_time(info.duration)} ({info.duration:.3f}s)")
        print(f"Kích thước : {info.width}x{info.height}")
        print(f"FPS        : {info.fps:.3f}")
        print(f"Video codec: {info.codec}")
        print(f"Audio codec: {info.audio_codec or '(không có)'}")
        return 0

    start = parse_timecode(args.start)
    if args.end is not None and args.duration is not None:
        raise SystemExit("Dùng --end hoặc --duration, không dùng cùng.")
    if args.duration is not None:
        end = start + parse_timecode(args.duration)
    elif args.end is not None:
        end = parse_timecode(args.end)
    else:
        info = probe(input_path)
        if info.duration <= 0:
            raise SystemExit("Không xác định được thời lượng. Cung cấp --end hoặc --duration.")
        end = info.duration

    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else _default_output(input_path, start, end)
    )

    opts = CutOptions(
        input_path=input_path,
        output_path=output,
        start=start,
        end=end,
        mode=args.mode,
        keep_audio=not args.no_audio,
        crf=args.crf,
        preset=args.preset,
        overwrite=not args.no_overwrite,
    )

    t0 = time.time()
    cut_video(opts, on_progress=_cli_progress)
    elapsed = time.time() - t0

    out_size = output.stat().st_size / (1024 * 1024)
    print("=" * 60)
    print("CẮT VIDEO – HOÀN TẤT")
    print(f"Đầu vào   : {input_path}")
    print(f"Đầu ra    : {output}")
    print(f"Đoạn      : {format_time(start)} → {format_time(end)} ({format_time(end - start)})")
    print(f"Chế độ    : {opts.mode}  | Tiếng: {'có' if opts.keep_audio else 'không'}")
    print(f"Dung lượng: {out_size:.2f} MB")
    print(f"Thời gian : {elapsed:.2f}s")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        raise SystemExit("Đã hủy")
