#!/usr/bin/env python3
"""
GIF Maker.

Features:
- Convert video, GIF, image sequence, or single image to GIF
- Platform presets (Instagram, TikTok, X, Discord, Telegram, WhatsApp, etc.)
- Resize modes: contain, cover, stretch
- Controls for FPS, quality, color count, loop, speed, trim, max size target
- Optional iterative optimization to fit a target file size
- Reusable build_gif() entry point used by both the CLI and the GUI
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

try:
    import cv2
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'opencv-python'. Run: pip install -r requirements.txt"
    ) from exc

try:
    from PIL import Image, ImageColor, ImageOps, ImageSequence
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'Pillow'. Run: pip install -r requirements.txt"
    ) from exc


IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
    ".gif",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".mpg",
    ".mpeg",
    ".3gp",
    ".wmv",
}


ProgressCallback = Callable[[str, float, str], None]
"""(stage, fraction_0_to_1, message) -> None"""


@dataclass(frozen=True)
class Preset:
    width: int
    height: int
    fps: float
    quality: int
    colors: int
    max_size_mb: Optional[float]
    description: str


PRESETS = {
    "instagram_post": Preset(1080, 1080, 15, 85, 220, 15.0, "Instagram feed square"),
    "instagram_story": Preset(1080, 1920, 15, 85, 220, 15.0, "Instagram story/reels"),
    "tiktok": Preset(1080, 1920, 15, 85, 220, 15.0, "TikTok vertical"),
    "x_post": Preset(1280, 720, 15, 80, 210, 15.0, "X/Twitter landscape"),
    "facebook": Preset(1200, 630, 15, 80, 210, 15.0, "Facebook feed"),
    "discord": Preset(640, 640, 20, 80, 220, 8.0, "Discord chat/server assets"),
    "telegram": Preset(720, 720, 20, 82, 220, 10.0, "Telegram optimized"),
    "whatsapp": Preset(512, 512, 15, 78, 200, 5.0, "WhatsApp share-friendly"),
    "reddit": Preset(1080, 1080, 15, 82, 220, 15.0, "Reddit square media"),
    "web_small": Preset(640, 360, 12, 72, 170, 3.0, "Website lightweight GIF"),
    "web_hd": Preset(1280, 720, 18, 82, 220, 12.0, "Website HD GIF"),
    "emoji": Preset(256, 256, 12, 75, 128, 1.0, "Emoji/sticker-like small GIF"),
}


@dataclass
class BuildConfig:
    input_path: Path
    output_path: Path
    width: Optional[int] = None
    height: Optional[int] = None
    fit: str = "contain"
    background: Optional[Tuple[int, int, int, int]] = (0, 0, 0, 255)
    fps: float = 15.0
    quality: int = 80
    colors: int = 200
    dither: int = 0  # PIL dither enum value
    start: float = 0.0
    end: Optional[float] = None
    duration: Optional[float] = None
    speed: float = 1.0
    loop: int = 0
    max_size_mb: Optional[float] = None
    max_frames: int = 900
    optimize: bool = True
    keep_alpha: bool = True
    palette_mode: str = "per_frame"  # 'per_frame' (fidelity) or 'global' (stability/size)


@dataclass
class BuildResult:
    output_path: Path
    width: int
    height: int
    frames: int
    fps: float
    duration_seconds: float
    file_size_mb: float
    colors_used: int
    target_size_mb: Optional[float]
    final_dither: int = field(default=0)


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------


def parse_timecode(value: str) -> float:
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError("Time value cannot be empty")

    if ":" not in value:
        try:
            seconds = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid time '{value}'. Use seconds or HH:MM:SS"
            ) from exc
        if seconds < 0:
            raise argparse.ArgumentTypeError("Time must be >= 0")
        return seconds

    parts = value.split(":")
    if len(parts) not in {2, 3}:
        raise argparse.ArgumentTypeError(
            f"Invalid time '{value}'. Expected MM:SS or HH:MM:SS"
        )

    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid time '{value}'. Time parts must be numeric"
        ) from exc

    if any(n < 0 for n in nums):
        raise argparse.ArgumentTypeError("Time parts must be >= 0")

    if len(nums) == 2:
        minutes, seconds = nums
        return minutes * 60 + seconds

    hours, minutes, seconds = nums
    return hours * 3600 + minutes * 60 + seconds


def parse_background(value: str) -> Optional[Tuple[int, int, int, int]]:
    if value.lower() == "transparent":
        return None
    try:
        rgb = ImageColor.getrgb(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid background color '{value}'. Example: '#000000' or 'white'"
        ) from exc

    if len(rgb) == 4:
        return rgb
    return (rgb[0], rgb[1], rgb[2], 255)


def list_presets() -> str:
    rows: List[str] = []
    for name, p in PRESETS.items():
        size = f"{p.width}x{p.height}"
        max_size = f"{p.max_size_mb:g}MB" if p.max_size_mb else "-"
        rows.append(
            f"  - {name:16} {size:11} {p.fps:>4.0f}fps  quality={p.quality:>2}  "
            f"colors={p.colors:>3}  max={max_size:>6}  {p.description}"
        )
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gif_tool.py",
        description=(
            "Create optimized GIFs with platform presets, size/FPS/quality controls, "
            "and file size targeting."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("-i", "--input", help="Input file (video/gif/image) or folder of image sequence")
    parser.add_argument("-o", "--output", help="Output GIF path (default: <input_name>.gif)")

    parser.add_argument(
        "--preset",
        choices=[*PRESETS.keys(), "custom"],
        default="custom",
        help=(
            "Platform preset\n"
            "Available:\n"
            f"{list_presets()}"
        ),
    )

    parser.add_argument("--width", type=int, help="Output width in pixels")
    parser.add_argument("--height", type=int, help="Output height in pixels")
    parser.add_argument(
        "--fit",
        choices=["contain", "cover", "stretch"],
        default="contain",
        help="Resize strategy when width and height are both set",
    )
    parser.add_argument(
        "--background",
        type=parse_background,
        default=(0, 0, 0, 255),
        help="Background color for contain mode. Use 'transparent' to preserve alpha.",
    )

    parser.add_argument("--fps", type=float, help="Output frames per second")
    parser.add_argument(
        "--quality",
        type=int,
        default=None,
        help="Visual quality 1-100 (higher = bigger file). Mapped to color count if --colors not set.",
    )
    parser.add_argument("--colors", type=int, help="Palette colors 2-256")
    parser.add_argument(
        "--dither",
        choices=["none", "floyd"],
        default="floyd",
        help="GIF dithering method",
    )
    parser.add_argument(
        "--palette",
        choices=["global", "per_frame"],
        default="per_frame",
        help=(
            "Palette strategy.\n"
            "  per_frame : independent palette per frame (best fidelity, default).\n"
            "  global    : one palette for the whole clip (less flicker, smaller files,\n"
            "              best for clips with stable color)."
        ),
    )

    parser.add_argument("--start", type=parse_timecode, default=0.0, help="Start time in seconds or HH:MM:SS")
    parser.add_argument("--end", type=parse_timecode, default=None, help="End time in seconds or HH:MM:SS")
    parser.add_argument("--duration", type=parse_timecode, default=None, help="Duration from start")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed factor. 2.0=faster, 0.5=slower")

    parser.add_argument("--loop", type=int, default=0, help="GIF loop count. 0=infinite")
    parser.add_argument("--max-size-mb", type=float, default=None, help="Target max output file size in MB")
    parser.add_argument(
        "--max-frames",
        type=int,
        default=900,
        help="Safety cap for output frames to avoid huge memory usage",
    )

    parser.add_argument(
        "--no-optimize",
        action="store_true",
        help="Disable iterative optimization for max-size target",
    )
    parser.add_argument(
        "--flatten-alpha",
        action="store_true",
        help="Force remove transparency and flatten on background color",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="Show all presets and exit",
    )

    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch graphical interface",
    )

    return parser


def sanitize_and_merge_args(args: argparse.Namespace) -> BuildConfig:
    if not args.input:
        raise SystemExit("Error: --input is required. Example: python gif_tool.py -i video.mp4")

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    preset = PRESETS.get(args.preset) if args.preset != "custom" else None

    width = args.width if args.width else (preset.width if preset else None)
    height = args.height if args.height else (preset.height if preset else None)

    fps = args.fps if args.fps else (preset.fps if preset else 15.0)
    quality = args.quality if args.quality is not None else (preset.quality if preset else 80)

    if not 1 <= quality <= 100:
        raise SystemExit("quality must be between 1 and 100")

    if args.colors is not None:
        if not 2 <= args.colors <= 256:
            raise SystemExit("colors must be between 2 and 256")
        colors = args.colors
    elif preset:
        colors = preset.colors
    else:
        colors = quality_to_colors(quality)

    max_size_mb = args.max_size_mb
    if max_size_mb is None and preset:
        max_size_mb = preset.max_size_mb

    if fps <= 0:
        raise SystemExit("fps must be > 0")
    if args.speed <= 0:
        raise SystemExit("speed must be > 0")
    if args.max_frames <= 0:
        raise SystemExit("max-frames must be > 0")
    if args.loop < 0:
        raise SystemExit("loop must be >= 0")

    start = args.start
    end = args.end
    duration = args.duration

    if duration is not None and end is not None:
        raise SystemExit("Use either --end or --duration, not both")

    if duration is not None:
        end = start + duration

    if end is not None and end <= start:
        raise SystemExit("end must be greater than start")

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else default_output_path(input_path)
    )

    dither_value = Image.Dither.FLOYDSTEINBERG if args.dither == "floyd" else Image.Dither.NONE

    keep_alpha = not args.flatten_alpha
    background = args.background
    if not keep_alpha and background is None:
        background = (0, 0, 0, 255)

    return BuildConfig(
        input_path=input_path,
        output_path=output_path,
        width=width,
        height=height,
        fit=args.fit,
        background=background,
        fps=fps,
        quality=quality,
        colors=colors,
        dither=dither_value,
        start=start,
        end=end,
        duration=duration,
        speed=args.speed,
        loop=args.loop,
        max_size_mb=max_size_mb,
        max_frames=args.max_frames,
        optimize=not args.no_optimize,
        keep_alpha=keep_alpha,
        palette_mode=args.palette,
    )


def quality_to_colors(quality: int) -> int:
    color_min = 32
    color_max = 256
    value = color_min + round((quality - 1) * (color_max - color_min) / 99)
    return max(2, min(256, value))


def default_output_path(input_path: Path) -> Path:
    if input_path.is_dir():
        return (input_path / "output.gif").resolve()
    return input_path.with_suffix(".gif").resolve()


def discover_input_kind(path: Path) -> str:
    if path.is_dir():
        return "sequence"
    ext = path.suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in IMAGE_EXTENSIONS:
        if ext == ".gif":
            return "gif"
        return "image"
    return "video"


def collect_sequence_images(folder: Path) -> List[Path]:
    images = [
        p for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not images:
        raise SystemExit(f"No image files found in folder: {folder}")
    return images


# ---------------------------------------------------------------------------
# Frame layout / decoding
# ---------------------------------------------------------------------------


def make_frame(img_bgra, cfg: BuildConfig) -> Image.Image:
    if img_bgra.ndim == 2:
        frame = Image.fromarray(img_bgra).convert("RGBA")
    elif img_bgra.shape[2] == 4:
        frame = Image.fromarray(cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2RGBA))
    else:
        frame = Image.fromarray(cv2.cvtColor(img_bgra, cv2.COLOR_BGR2RGBA))
    return apply_layout(frame, cfg)


def apply_layout(frame: Image.Image, cfg: BuildConfig) -> Image.Image:
    src = frame.convert("RGBA")

    if cfg.width is None and cfg.height is None:
        out = src
    elif cfg.width is not None and cfg.height is not None:
        out = resize_with_fit(src, cfg.width, cfg.height, cfg.fit, cfg.background)
    else:
        w, h = src.size
        if cfg.width is not None:
            new_w = cfg.width
            new_h = max(1, round(h * (new_w / w)))
        else:
            new_h = cfg.height if cfg.height else h
            new_w = max(1, round(w * (new_h / h)))
        out = src.resize((new_w, new_h), Image.Resampling.LANCZOS)

    if not cfg.keep_alpha:
        bg = cfg.background if cfg.background else (0, 0, 0, 255)
        base = Image.new("RGBA", out.size, bg)
        base.alpha_composite(out)
        return base.convert("RGB").convert("RGBA")

    return out


def resize_with_fit(
    src: Image.Image,
    width: int,
    height: int,
    fit_mode: str,
    background: Optional[Tuple[int, int, int, int]],
) -> Image.Image:
    if fit_mode == "stretch":
        return src.resize((width, height), Image.Resampling.LANCZOS)

    if fit_mode == "cover":
        return ImageOps.fit(
            src, (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    # contain
    resized = src.copy()
    resized.thumbnail((width, height), Image.Resampling.LANCZOS)

    if background is None:
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    else:
        canvas = Image.new("RGBA", (width, height), background)

    left = (width - resized.width) // 2
    top = (height - resized.height) // 2
    canvas.paste(resized, (left, top), resized)
    return canvas


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------


def extract_from_video(cfg: BuildConfig, on_progress: Optional[ProgressCallback] = None) -> List[Image.Image]:
    cap = cv2.VideoCapture(str(cfg.input_path))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {cfg.input_path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS)
    if not src_fps or src_fps <= 0:
        src_fps = 30.0

    frame_count_raw = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    frame_count = int(frame_count_raw) if frame_count_raw and frame_count_raw > 0 else 0
    total_duration = frame_count / src_fps if frame_count > 0 else None

    start_sec = max(0.0, cfg.start)
    end_sec = cfg.end if cfg.end is not None else total_duration
    if end_sec is None:
        end_sec = float("inf")

    output_interval = cfg.speed / cfg.fps
    next_capture_time = start_sec

    frames: List[Image.Image] = []
    index = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        current_time = index / src_fps
        index += 1

        if current_time < start_sec:
            continue
        if current_time > end_sec:
            break

        while current_time + 1e-9 >= next_capture_time:
            frames.append(make_frame(frame, cfg))
            if on_progress and (len(frames) % 5 == 0):
                # progress fraction is unknown when end_sec is inf; clamp to 0.99
                if math.isfinite(end_sec) and end_sec > start_sec:
                    f = min(0.99, (next_capture_time - start_sec) / (end_sec - start_sec))
                else:
                    f = min(0.99, len(frames) / max(cfg.max_frames, 1))
                on_progress("decode", f, f"Decoded {len(frames)} frames")

            if len(frames) >= cfg.max_frames:
                cap.release()
                return frames
            next_capture_time += output_interval
            if next_capture_time > end_sec:
                cap.release()
                return frames

    cap.release()
    return frames


def extract_from_gif(cfg: BuildConfig, on_progress: Optional[ProgressCallback] = None) -> List[Image.Image]:
    with Image.open(cfg.input_path) as im:
        raw_frames: List[Image.Image] = []
        durations: List[float] = []
        for frm in ImageSequence.Iterator(im):
            raw_frames.append(frm.convert("RGBA"))
            duration_ms = frm.info.get("duration", im.info.get("duration", 100))
            durations.append(max(10, int(duration_ms)) / 1000.0)

    total_duration = sum(durations)
    start_sec = max(0.0, cfg.start)
    end_sec = cfg.end if cfg.end is not None else total_duration
    end_sec = min(end_sec, total_duration)

    if end_sec <= start_sec:
        return []

    output_interval = cfg.speed / cfg.fps
    next_capture_time = start_sec

    frames: List[Image.Image] = []
    t = 0.0

    for idx, (frm, dur) in enumerate(zip(raw_frames, durations)):
        frame_start = t
        frame_end = t + dur
        t = frame_end

        if frame_end < start_sec:
            continue
        if frame_start > end_sec:
            break

        while next_capture_time <= frame_end + 1e-9 and next_capture_time <= end_sec:
            frames.append(apply_layout(frm, cfg))
            if on_progress:
                f = min(0.99, (idx + 1) / len(raw_frames))
                on_progress("decode", f, f"Decoded {len(frames)} frames")
            if len(frames) >= cfg.max_frames:
                return frames
            next_capture_time += output_interval

    return frames


def extract_from_image(cfg: BuildConfig, on_progress: Optional[ProgressCallback] = None) -> List[Image.Image]:
    with Image.open(cfg.input_path) as im:
        frame = apply_layout(im.convert("RGBA"), cfg)
    if on_progress:
        on_progress("decode", 1.0, "Loaded single image")
    return [frame, frame.copy()]


def extract_from_sequence(cfg: BuildConfig, on_progress: Optional[ProgressCallback] = None) -> List[Image.Image]:
    files = collect_sequence_images(cfg.input_path)

    source_fps = 30.0
    start_index = max(0, int(math.floor(cfg.start * source_fps)))

    end_index: Optional[int] = None
    if cfg.end is not None:
        end_index = max(0, int(math.ceil(cfg.end * source_fps)))
    elif cfg.duration is not None:
        end_index = max(0, int(math.ceil((cfg.start + cfg.duration) * source_fps)))

    selected_files = files[start_index:end_index] if end_index is not None else files[start_index:]
    if not selected_files:
        return []

    output_interval = cfg.speed / cfg.fps
    next_capture_time = cfg.start

    frames: List[Image.Image] = []

    for idx, file_path in enumerate(selected_files):
        current_time = (start_index + idx) / source_fps
        if current_time + 1e-9 < next_capture_time:
            continue

        with Image.open(file_path) as im:
            frame = apply_layout(im.convert("RGBA"), cfg)

        while current_time + 1e-9 >= next_capture_time:
            frames.append(frame.copy())
            if on_progress:
                f = min(0.99, (idx + 1) / len(selected_files))
                on_progress("decode", f, f"Decoded {len(frames)} frames")
            if len(frames) >= cfg.max_frames:
                return frames
            next_capture_time += output_interval

    return frames


def extract_frames(cfg: BuildConfig, on_progress: Optional[ProgressCallback] = None) -> List[Image.Image]:
    kind = discover_input_kind(cfg.input_path)

    if on_progress:
        on_progress("decode", 0.0, f"Reading input ({kind})")

    if kind == "video":
        return extract_from_video(cfg, on_progress)
    if kind == "gif":
        return extract_from_gif(cfg, on_progress)
    if kind == "sequence":
        return extract_from_sequence(cfg, on_progress)
    return extract_from_image(cfg, on_progress)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _best_quantize_method() -> int:
    """Chọn quantizer chất lượng tốt nhất Pillow đang có sẵn.

    Ưu tiên LIBIMAGEQUANT (chất lượng cao nhất, mượt gradient).
    Fallback MEDIANCUT (mặc định của Pillow, ổn định và tốt).
    Tránh FASTOCTREE vì cho ra dải màu thô, dễ "bệt" / nhòe.
    """
    try:
        from PIL import features

        if features.check("libimagequant"):
            return Image.Quantize.LIBIMAGEQUANT
    except Exception:
        pass
    return Image.Quantize.MEDIANCUT


_QUANTIZE_METHOD = _best_quantize_method()


def _flatten_for_palette(img: Image.Image, background: Optional[Tuple[int, int, int, int]]) -> Image.Image:
    """Trộn alpha lên nền cho đúng trước khi xây palette.

    Nếu giữ alpha thì giữ nguyên RGBA, ngược lại flatten về RGB để palette
    không phải dành màu cho vùng trong suốt.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    if background is None:
        return img  # giữ alpha
    base = Image.new("RGBA", img.size, background)
    base.alpha_composite(img)
    return base.convert("RGB")


def _build_global_palette(
    frames: Sequence[Image.Image],
    colors: int,
    keep_alpha: bool,
    background: Optional[Tuple[int, int, int, int]],
) -> Image.Image:
    """Tạo bảng màu toàn cục bằng cách lấy mẫu nhiều frame rồi quantize 1 lần.

    Trả về một ảnh palettized dùng làm "khuôn" — chính ảnh này sẽ được dùng
    làm template cho `Image.quantize(palette=...)` ở mọi frame.
    """
    if not frames:
        raise ValueError("Không có frame để xây palette")

    # Lấy ~24 frame trải đều để hạn chế bộ nhớ với video dài.
    sample_n = min(len(frames), 24)
    if sample_n <= 0:
        sample_n = 1
    if len(frames) <= sample_n:
        samples = list(frames)
    else:
        step = len(frames) / sample_n
        samples = [frames[int(i * step)] for i in range(sample_n)]

    # Ghép mosaic theo cột để Pillow nhìn 1 ảnh duy nhất khi dựng palette.
    base_w, base_h = samples[0].size
    mosaic = Image.new("RGBA", (base_w, base_h * len(samples)), (0, 0, 0, 0))
    y = 0
    for s in samples:
        if s.size != (base_w, base_h):
            s = s.resize((base_w, base_h), Image.Resampling.LANCZOS)
        mosaic.paste(s, (0, y))
        y += base_h

    bg = None if keep_alpha else (background if background else (0, 0, 0, 255))
    flat = _flatten_for_palette(mosaic, bg)

    # libimagequant chỉ hoạt động trên RGBA; mediancut hoạt động trên RGB.
    method = _QUANTIZE_METHOD
    if method == Image.Quantize.LIBIMAGEQUANT and flat.mode != "RGBA":
        flat = flat.convert("RGBA")
    elif method != Image.Quantize.LIBIMAGEQUANT and flat.mode == "RGBA":
        # Nền None nghĩa là người dùng muốn giữ alpha; vẫn cần RGB cho mediancut.
        flat = flat.convert("RGB")

    return flat.quantize(colors=colors, method=method, dither=Image.Dither.NONE)


def quantize_frames(
    frames: Sequence[Image.Image],
    colors: int,
    dither: int,
    keep_alpha: bool,
    on_progress: Optional[ProgressCallback] = None,
    palette_mode: str = "global",
    background: Optional[Tuple[int, int, int, int]] = None,
) -> List[Image.Image]:
    """Quantize danh sách frame.

    palette_mode='global' (khuyến nghị): xây 1 bảng màu chung từ các frame mẫu,
    áp đồng nhất lên mọi frame. Cho chất lượng tốt hơn rõ rệt cho video, đỡ
    flicker và đỡ "nhòe màu".

    palette_mode='per_frame': mỗi frame có palette riêng. Linh hoạt hơn cho
    cảnh đổi màu mạnh nhưng dễ flicker và file thường lớn hơn.
    """
    out: List[Image.Image] = []
    total = max(1, len(frames))

    if palette_mode == "global":
        if on_progress:
            on_progress("quantize", 0.0, "Building global palette...")
        palette_image = _build_global_palette(frames, colors, keep_alpha, background)

        bg = None if keep_alpha else (background if background else (0, 0, 0, 255))
        for i, frm in enumerate(frames):
            flat = _flatten_for_palette(frm, bg)
            if flat.mode == "RGBA":
                flat = flat.convert("RGB")
            quantized = flat.quantize(palette=palette_image, dither=dither)
            out.append(quantized)
            if on_progress and (i % 5 == 0 or i == total - 1):
                on_progress("quantize", (i + 1) / total, f"Apply palette {i + 1}/{total}")
        return out

    # per_frame
    method = _QUANTIZE_METHOD
    bg = None if keep_alpha else (background if background else (0, 0, 0, 255))
    for i, frm in enumerate(frames):
        flat = _flatten_for_palette(frm, bg)
        if method == Image.Quantize.LIBIMAGEQUANT and flat.mode != "RGBA":
            flat = flat.convert("RGBA")
        elif method != Image.Quantize.LIBIMAGEQUANT and flat.mode == "RGBA":
            flat = flat.convert("RGB")
        quantized = flat.quantize(colors=colors, method=method, dither=dither)
        out.append(quantized)
        if on_progress and (i % 5 == 0 or i == total - 1):
            on_progress("quantize", (i + 1) / total, f"Quantize {i + 1}/{total}")
    return out


def write_gif(
    frames: Sequence[Image.Image],
    output_path: Path,
    fps: float,
    loop: int,
    optimize: bool,
) -> None:
    if not frames:
        raise SystemExit("No frames generated from input. Check trim/time options.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    duration_ms = max(10, int(round(1000 / fps)))

    first, *rest = frames
    first.save(
        output_path,
        save_all=True,
        append_images=rest,
        duration=duration_ms,
        loop=loop,
        optimize=optimize,
        disposal=2,
    )


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


# ---------------------------------------------------------------------------
# High-level pipeline
# ---------------------------------------------------------------------------


def build_gif(cfg: BuildConfig, on_progress: Optional[ProgressCallback] = None) -> BuildResult:
    """End-to-end pipeline. Used by both CLI and GUI."""

    frames = extract_frames(cfg, on_progress)
    if not frames:
        raise SystemExit("No frames generated. Check input and trim settings.")

    if cfg.max_size_mb is not None and cfg.optimize:
        return _encode_with_size_target(frames, cfg, on_progress)

    if on_progress:
        on_progress("quantize", 0.0, "Quantizing colors")
    q_frames = quantize_frames(
        frames, cfg.colors, cfg.dither, cfg.keep_alpha, on_progress,
        palette_mode=cfg.palette_mode, background=cfg.background,
    )

    if on_progress:
        on_progress("encode", 0.5, "Writing GIF")
    write_gif(q_frames, cfg.output_path, cfg.fps, cfg.loop, optimize=cfg.optimize)

    if on_progress:
        on_progress("done", 1.0, "Finished")

    first = q_frames[0]
    return BuildResult(
        output_path=cfg.output_path,
        width=first.width,
        height=first.height,
        frames=len(q_frames),
        fps=cfg.fps,
        duration_seconds=len(q_frames) / cfg.fps,
        file_size_mb=file_size_mb(cfg.output_path),
        colors_used=cfg.colors,
        target_size_mb=cfg.max_size_mb,
        final_dither=cfg.dither,
    )


def _reduction_factor(ratio: float) -> float:
    """Pick how aggressively to reduce based on how far we are from target.

    ratio = current_size_mb / target_size_mb (>1.0 means too big).
    Returns multiplier in (0, 1] to scale a knob (smaller = more aggressive).
    """
    if ratio <= 1.0:
        return 1.0
    if ratio >= 4.0:
        return 0.55
    if ratio >= 2.0:
        return 0.7
    if ratio >= 1.4:
        return 0.82
    return 0.92


def _encode_with_size_target(
    frames: List[Image.Image],
    cfg: BuildConfig,
    on_progress: Optional[ProgressCallback] = None,
) -> BuildResult:
    """Iteratively reduce colors, fps, and dimensions to hit max_size_mb.

    Strategy: rebalance the three knobs each round, with reduction strength
    proportional to how far we are from the target. Stop early on success.
    """

    work_frames = frames
    work_fps = cfg.fps
    work_colors = cfg.colors
    rounds = 12

    target = cfg.max_size_mb
    assert target is not None

    min_colors = 16
    min_fps = 6.0
    min_dim = 64

    last_q_frames: List[Image.Image] = []
    last_size_mb = float("inf")

    for r in range(rounds):
        if on_progress:
            on_progress(
                "optimize",
                (r + 0.1) / rounds,
                (
                    f"Optimize round {r + 1}/{rounds}: "
                    f"colors={work_colors} fps={work_fps:.1f} "
                    f"size={work_frames[0].width}x{work_frames[0].height}"
                ),
            )

        q_frames = quantize_frames(
            work_frames, work_colors, cfg.dither, cfg.keep_alpha, None,
            palette_mode=cfg.palette_mode, background=cfg.background,
        )
        write_gif(q_frames, cfg.output_path, work_fps, cfg.loop, optimize=cfg.optimize)
        size_mb = file_size_mb(cfg.output_path)

        last_q_frames = q_frames
        last_size_mb = size_mb

        if on_progress:
            on_progress(
                "optimize",
                (r + 1) / rounds,
                f"Round {r + 1}: {size_mb:.2f} MB (target {target:g} MB)",
            )

        if size_mb <= target:
            break

        ratio = size_mb / target
        factor = _reduction_factor(ratio)

        # Reduce colors first when above min, otherwise reduce fps,
        # otherwise scale frames down. When the ratio is large, apply
        # multiple reductions in the same round to converge faster.
        applied = False

        if work_colors > min_colors:
            new_colors = max(min_colors, int(work_colors * factor))
            if new_colors < work_colors:
                work_colors = new_colors
                applied = True

        if (ratio >= 2.0 or work_colors <= min_colors) and work_fps > min_fps:
            new_fps = max(min_fps, work_fps * factor)
            if new_fps < work_fps - 1e-3:
                work_fps = new_fps
                applied = True

        if (ratio >= 3.0 or (work_colors <= min_colors and work_fps <= min_fps)):
            cur_w = work_frames[0].width
            cur_h = work_frames[0].height
            if cur_w > min_dim and cur_h > min_dim:
                scale = factor if factor < 0.95 else 0.9
                nw = max(min_dim, int(cur_w * scale))
                nh = max(min_dim, int(cur_h * scale))
                if (nw, nh) != (cur_w, cur_h):
                    work_frames = [
                        frm.resize((nw, nh), Image.Resampling.LANCZOS)
                        for frm in work_frames
                    ]
                    applied = True

        if not applied:
            # Already at floor for every knob; nothing more to try.
            break

    if on_progress:
        on_progress("done", 1.0, f"Finished at {last_size_mb:.2f} MB")

    first = last_q_frames[0]
    return BuildResult(
        output_path=cfg.output_path,
        width=first.width,
        height=first.height,
        frames=len(last_q_frames),
        fps=work_fps,
        duration_seconds=len(last_q_frames) / work_fps,
        file_size_mb=last_size_mb,
        colors_used=work_colors,
        target_size_mb=cfg.max_size_mb,
        final_dither=cfg.dither,
    )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def print_summary(cfg: BuildConfig, result: BuildResult) -> None:
    max_size = f"{result.target_size_mb:g}MB" if result.target_size_mb is not None else "-"

    print("=" * 70)
    print("GIF CREATED")
    print("-" * 70)
    print(f"Input       : {cfg.input_path}")
    print(f"Output      : {result.output_path}")
    print(f"Resolution  : {result.width}x{result.height}")
    print(f"Frames      : {result.frames}")
    print(f"FPS         : {result.fps:.2f}")
    print(f"Duration    : {result.duration_seconds:.2f}s")
    print(f"Colors used : up to {result.colors_used}")
    print(f"Loop        : {'infinite' if cfg.loop == 0 else cfg.loop}")
    print(f"Target size : {max_size}")
    print(f"File size   : {result.file_size_mb:.2f}MB")
    print("=" * 70)


def _cli_progress(stage: str, fraction: float, message: str) -> None:
    pct = int(fraction * 100)
    sys.stdout.write(f"\r[{stage:8}] {pct:3d}%  {message[:60]:<60}")
    sys.stdout.flush()
    if stage == "done" or pct >= 100:
        sys.stdout.write("\n")
        sys.stdout.flush()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_presets:
        print(list_presets())
        return 0

    if args.gui:
        try:
            from gif_gui import launch  # type: ignore
        except ImportError as exc:
            raise SystemExit(f"GUI not available: {exc}")
        launch()
        return 0

    cfg = sanitize_and_merge_args(args)
    result = build_gif(cfg, on_progress=_cli_progress)
    print_summary(cfg, result)
    return 0


# expose a convenience helper for the GUI
def make_default_config(input_path: Path, output_path: Optional[Path] = None) -> BuildConfig:
    return BuildConfig(
        input_path=input_path,
        output_path=output_path or default_output_path(input_path),
        dither=Image.Dither.FLOYDSTEINBERG,
    )


def with_preset(cfg: BuildConfig, preset_name: str) -> BuildConfig:
    if preset_name == "custom" or preset_name not in PRESETS:
        return cfg
    p = PRESETS[preset_name]
    return replace(
        cfg,
        width=p.width,
        height=p.height,
        fps=p.fps,
        quality=p.quality,
        colors=p.colors,
        max_size_mb=p.max_size_mb,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        raise SystemExit("Interrupted by user")
