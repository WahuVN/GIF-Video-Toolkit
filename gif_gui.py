#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Giao diện đồ họa cho gif_tool.

Tính năng:
- Kéo thả tệp/thư mục vào cửa sổ (cần tkinterdnd2)
- Xem trước video, tua, phát/dừng ngay trong app
- Đặt điểm vào / điểm ra trực quan trên thanh thời gian
- Toàn bộ giao diện tiếng Việt có dấu (UTF-8)
- Hỗ trợ video, GIF động, ảnh, hoặc thư mục chuỗi ảnh

Chạy:
    python gif_gui.py
hoặc:
    python gif_tool.py --gui
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageSequence, ImageTk

from gif_tool import (
    PRESETS,
    BuildConfig,
    BuildResult,
    build_gif,
    default_output_path,
    discover_input_kind,
    parse_background,
    parse_timecode,
    quality_to_colors,
)

# tkinterdnd2 là tùy chọn: nếu có thì bật kéo thả, không có thì vẫn chạy bình thường.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore

    DND_AVAILABLE = True
except ImportError:  # pragma: no cover
    DND_AVAILABLE = False
    DND_FILES = None  # type: ignore
    TkinterDnD = None  # type: ignore


APP_TITLE = "Công cụ tạo GIF"
PRESET_OPTIONS: List[Tuple[str, str]] = [
    ("custom", "Tùy chỉnh"),
    ("instagram_post", "Instagram – Bài vuông 1080×1080"),
    ("instagram_story", "Instagram – Story 1080×1920"),
    ("tiktok", "TikTok 1080×1920"),
    ("x_post", "X / Twitter 1280×720"),
    ("facebook", "Facebook 1200×630"),
    ("discord", "Discord 640×640"),
    ("telegram", "Telegram 720×720"),
    ("whatsapp", "WhatsApp 512×512"),
    ("reddit", "Reddit 1080×1080"),
    ("web_small", "Web nhẹ 640×360"),
    ("web_hd", "Web HD 1280×720"),
    ("emoji", "Emoji / Sticker 256×256"),
]
FIT_OPTIONS = [
    ("contain", "Vừa khung (chừa lề)"),
    ("cover", "Phủ khung (cắt thừa)"),
    ("stretch", "Kéo căng"),
]
DITHER_OPTIONS = [
    ("floyd", "Floyd–Steinberg (mịn)"),
    ("none", "Không (sắc nét)"),
]


# ===========================================================================
# Trình mở video / nguồn ảnh
# ===========================================================================


class MediaSource:
    """Trừu tượng hóa nguồn xem trước. Hỗ trợ video, GIF động, ảnh tĩnh, chuỗi ảnh."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.kind = discover_input_kind(path)
        self.duration: float = 0.0
        self.fps: float = 0.0
        self.frame_count: int = 0
        self.width: int = 0
        self.height: int = 0
        self._cap: Optional[cv2.VideoCapture] = None
        self._gif_frames: List[Image.Image] = []
        self._gif_durations: List[float] = []
        self._sequence: List[Path] = []
        self._image: Optional[Image.Image] = None

        if self.kind == "video":
            self._open_video()
        elif self.kind == "gif":
            self._open_gif()
        elif self.kind == "image":
            self._open_image()
        elif self.kind == "sequence":
            self._open_sequence()

    # --- mở từng loại ---------------------------------------------------

    def _open_video(self) -> None:
        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            raise RuntimeError(f"Không mở được video: {self.path}")
        self._cap = cap
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if self.fps <= 0:
            self.fps = 30.0
        self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.duration = self.frame_count / self.fps if self.frame_count else 0.0
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

    def _open_gif(self) -> None:
        with Image.open(self.path) as im:
            for frm in ImageSequence.Iterator(im):
                self._gif_frames.append(frm.convert("RGBA"))
                dur = frm.info.get("duration", im.info.get("duration", 100))
                self._gif_durations.append(max(10, int(dur)) / 1000.0)
        self.frame_count = len(self._gif_frames)
        self.duration = sum(self._gif_durations)
        if self.duration > 0 and self.frame_count > 0:
            self.fps = self.frame_count / self.duration
        else:
            self.fps = 10.0
        if self._gif_frames:
            self.width, self.height = self._gif_frames[0].size

    def _open_image(self) -> None:
        with Image.open(self.path) as im:
            self._image = im.convert("RGBA")
        self.width, self.height = self._image.size
        self.fps = 1.0
        self.frame_count = 1
        self.duration = 1.0

    def _open_sequence(self) -> None:
        from gif_tool import IMAGE_EXTENSIONS

        files = sorted(
            p for p in self.path.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        )
        if not files:
            raise RuntimeError("Thư mục không chứa ảnh")
        self._sequence = files
        self.fps = 30.0
        self.frame_count = len(files)
        self.duration = len(files) / self.fps
        with Image.open(files[0]) as im:
            self.width, self.height = im.size

    # --- lấy frame tại thời điểm t -------------------------------------

    def frame_at(self, t_seconds: float) -> Optional[Image.Image]:
        if self.kind == "video":
            return self._frame_at_video(t_seconds)
        if self.kind == "gif":
            return self._frame_at_gif(t_seconds)
        if self.kind == "sequence":
            return self._frame_at_sequence(t_seconds)
        return self._image.copy() if self._image else None

    def _frame_at_video(self, t_seconds: float) -> Optional[Image.Image]:
        if not self._cap:
            return None
        target = max(0.0, min(t_seconds, max(0.0, self.duration - 1e-3)))
        self._cap.set(cv2.CAP_PROP_POS_MSEC, target * 1000.0)
        ok, bgr = self._cap.read()
        if not ok or bgr is None:
            return None
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    def _frame_at_gif(self, t_seconds: float) -> Optional[Image.Image]:
        if not self._gif_frames:
            return None
        if self.duration <= 0:
            return self._gif_frames[0]
        t = max(0.0, min(t_seconds, self.duration - 1e-6))
        acc = 0.0
        for frm, dur in zip(self._gif_frames, self._gif_durations):
            acc += dur
            if t <= acc:
                return frm
        return self._gif_frames[-1]

    def _frame_at_sequence(self, t_seconds: float) -> Optional[Image.Image]:
        if not self._sequence:
            return None
        idx = int(t_seconds * self.fps)
        idx = max(0, min(idx, len(self._sequence) - 1))
        with Image.open(self._sequence[idx]) as im:
            return im.convert("RGBA")

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


# ===========================================================================
# Thanh trượt phạm vi (in/out) trên timeline
# ===========================================================================


class RangeSlider(tk.Canvas):
    """Thanh trượt có hai đầu để chọn khoảng thời gian.

    Bên cạnh đó còn vẽ một con trỏ phụ chỉ vị trí "đầu phát hiện tại"
    (do người dùng kéo trên thanh thời gian xem trước).
    """

    HANDLE_W = 10
    BAR_HEIGHT = 8
    PAD_X = 12
    HEIGHT = 38

    def __init__(self, master: tk.Misc) -> None:
        try:
            bg = master.cget("background")
        except tk.TclError:
            bg = master.tk.call("ttk::style", "lookup", "TFrame", "-background") or "#f0f0f0"
        super().__init__(master, height=self.HEIGHT, bg=bg, highlightthickness=0)
        self._min = 0.0
        self._max = 1.0
        self._lo = 0.0
        self._hi = 1.0
        self._cursor: Optional[float] = None
        self._dragging: Optional[str] = None
        self._on_change = lambda lo, hi: None
        self._on_cursor_change = lambda t: None

        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    # --- API ------------------------------------------------------------

    def set_bounds(self, min_v: float, max_v: float) -> None:
        if max_v <= min_v:
            max_v = min_v + 1.0
        self._min, self._max = float(min_v), float(max_v)
        self._lo = self._min
        self._hi = self._max
        self._cursor = self._min
        self._redraw()

    def set_range(self, lo: float, hi: float) -> None:
        self._lo = max(self._min, min(lo, self._max))
        self._hi = max(self._lo, min(hi, self._max))
        self._redraw()

    def set_cursor(self, t: float) -> None:
        self._cursor = max(self._min, min(t, self._max))
        self._redraw()

    def get_range(self) -> Tuple[float, float]:
        return self._lo, self._hi

    def on_change(self, callback) -> None:
        self._on_change = callback

    def on_cursor_change(self, callback) -> None:
        self._on_cursor_change = callback

    # --- vẽ -------------------------------------------------------------

    def _track_x(self, t: float) -> int:
        w = max(1, self.winfo_width() - 2 * self.PAD_X)
        if self._max == self._min:
            return self.PAD_X
        return self.PAD_X + int(round(w * (t - self._min) / (self._max - self._min)))

    def _x_to_t(self, x: int) -> float:
        w = max(1, self.winfo_width() - 2 * self.PAD_X)
        ratio = (x - self.PAD_X) / w
        return max(self._min, min(self._max, self._min + ratio * (self._max - self._min)))

    def _redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        if w <= 1:
            return
        cy = self.HEIGHT // 2

        # nền
        self.create_rectangle(
            self.PAD_X, cy - self.BAR_HEIGHT // 2,
            w - self.PAD_X, cy + self.BAR_HEIGHT // 2,
            fill="#dddddd", outline="",
        )

        x_lo = self._track_x(self._lo)
        x_hi = self._track_x(self._hi)

        # vùng được chọn
        self.create_rectangle(
            x_lo, cy - self.BAR_HEIGHT // 2,
            x_hi, cy + self.BAR_HEIGHT // 2,
            fill="#3a8dde", outline="",
        )

        # con trỏ vị trí xem trước
        if self._cursor is not None:
            xc = self._track_x(self._cursor)
            self.create_line(xc, 4, xc, self.HEIGHT - 4, fill="#e23a3a", width=2)

        # tay cầm
        self._draw_handle(x_lo, cy, "#1d6ec7")
        self._draw_handle(x_hi, cy, "#1d6ec7")

    def _draw_handle(self, x: int, cy: int, color: str) -> None:
        r = self.HANDLE_W // 2
        self.create_rectangle(
            x - r, cy - self.HEIGHT // 2 + 4,
            x + r, cy + self.HEIGHT // 2 - 4,
            fill=color, outline="#0e4a8a",
        )

    # --- chuột ----------------------------------------------------------

    def _on_press(self, event: tk.Event) -> None:
        x_lo = self._track_x(self._lo)
        x_hi = self._track_x(self._hi)
        d_lo = abs(event.x - x_lo)
        d_hi = abs(event.x - x_hi)

        # Ưu tiên tay cầm gần nhất; nếu trùng thì kéo tay cầm ở phía
        # tương ứng với chiều di chuyển.
        if d_lo <= 8 or d_hi <= 8:
            self._dragging = "lo" if d_lo <= d_hi else "hi"
        else:
            # Click vào thân thanh trượt: di chuyển con trỏ vị trí xem.
            self._dragging = "cursor"
            self._cursor = self._x_to_t(event.x)
            self._on_cursor_change(self._cursor)
            self._redraw()

    def _on_drag(self, event: tk.Event) -> None:
        if not self._dragging:
            return
        t = self._x_to_t(event.x)
        if self._dragging == "lo":
            self._lo = min(t, self._hi - 1e-3)
            self._on_change(self._lo, self._hi)
        elif self._dragging == "hi":
            self._hi = max(t, self._lo + 1e-3)
            self._on_change(self._lo, self._hi)
        else:
            self._cursor = t
            self._on_cursor_change(self._cursor)
        self._redraw()

    def _on_release(self, _event: tk.Event) -> None:
        self._dragging = None


# ===========================================================================
# Ứng dụng chính
# ===========================================================================


_BaseTk = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk


class App(_BaseTk):  # type: ignore[misc, valid-type]
    PREVIEW_W = 480
    PREVIEW_H = 270

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x780")
        self.minsize(980, 720)

        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._media: Optional[MediaSource] = None
        self._preview_imgtk: Optional[ImageTk.PhotoImage] = None

        self._playing = False
        self._play_after_id: Optional[str] = None
        self._cursor_time = 0.0
        self._suspend_text_trace = False

        self._build_styles()
        self._build_widgets()
        self._enable_dnd()
        self._bind_shortcuts()

        self._update_preset_label()
        self.after(80, self._drain_queue)

    # ------------------------------------------------------------------
    # Style + widgets
    # ------------------------------------------------------------------

    def _build_styles(self) -> None:
        try:
            style = ttk.Style(self)
            for theme in ("vista", "clam"):
                if theme in style.theme_names():
                    style.theme_use(theme)
                    break
        except tk.TclError:
            pass

    def _build_widgets(self) -> None:
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True, padx=10, pady=10)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        # --- Khu vực kéo thả + chọn file ----------------------------------
        io = ttk.LabelFrame(root, text="Tệp đầu vào / đầu ra")
        io.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for i in range(5):
            io.columnconfigure(i, weight=1 if i == 1 else 0)

        ttk.Label(io, text="Tệp / Thư mục:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.var_input = tk.StringVar()
        self.entry_input = ttk.Entry(io, textvariable=self.var_input)
        self.entry_input.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        self.btn_clear_input = ttk.Button(io, text="✕", width=3, command=self._clear_input)
        self.btn_clear_input.grid(row=0, column=2, padx=(0, 4))
        ttk.Button(io, text="Chọn tệp…", command=self._pick_input_file).grid(row=0, column=3, padx=4)
        ttk.Button(io, text="Chọn thư mục…", command=self._pick_input_folder).grid(row=0, column=4, padx=(0, 6))

        ttk.Label(io, text="Lưu GIF tại:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.var_output = tk.StringVar()
        self.entry_output = ttk.Entry(io, textvariable=self.var_output)
        self.entry_output.grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(io, text="✕", width=3, command=lambda: self.var_output.set("")).grid(row=1, column=2, padx=(0, 4))
        ttk.Button(io, text="Lưu thành…", command=self._pick_output).grid(row=1, column=3, padx=4)

        dnd_text = (
            "Mẹo: kéo thả tệp video / GIF / ảnh, hoặc thả cả thư mục ảnh vào đây."
            if DND_AVAILABLE
            else "Để bật kéo thả, cài đặt: pip install tkinterdnd2"
        )
        self.lbl_dnd = ttk.Label(io, text=dnd_text, foreground="#666")
        self.lbl_dnd.grid(row=2, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 4))

        self.var_input.trace_add("write", self._on_input_changed)

        # --- Cột trái: xem trước ------------------------------------------
        left = ttk.LabelFrame(root, text="Xem trước & chọn đoạn")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(
            left, width=self.PREVIEW_W, height=self.PREVIEW_H,
            bg="#101010", highlightthickness=0,
        )
        self.preview_canvas.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._draw_preview_placeholder("Chưa có tệp nào – hãy chọn hoặc kéo thả vào.")

        info_row = ttk.Frame(left)
        info_row.grid(row=1, column=0, sticky="ew", padx=8)
        self.var_time_text = tk.StringVar(value="00:00.00 / 00:00.00")
        ttk.Label(info_row, textvariable=self.var_time_text).pack(side="left")
        self.var_media_info = tk.StringVar(value="")
        ttk.Label(info_row, textvariable=self.var_media_info, foreground="#555").pack(side="right")

        self.range_slider = RangeSlider(left)
        self.range_slider.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 0))
        self.range_slider.on_change(self._on_range_changed)
        self.range_slider.on_cursor_change(self._on_cursor_changed)

        controls = ttk.Frame(left)
        controls.grid(row=3, column=0, sticky="ew", padx=8, pady=6)

        self.btn_play = ttk.Button(controls, text="▶  Phát", width=10, command=self._toggle_play)
        self.btn_play.pack(side="left")
        ttk.Button(controls, text="⏮  Đầu", width=8, command=lambda: self._seek_to(0.0)).pack(side="left", padx=4)
        ttk.Button(
            controls, text="⏭  Cuối", width=8,
            command=lambda: self._seek_to(self._media.duration if self._media else 0.0),
        ).pack(side="left")

        ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Button(controls, text="−1s", width=4, command=lambda: self._seek_relative(-1.0)).pack(side="left")
        ttk.Button(controls, text="−0.1s", width=5, command=lambda: self._seek_relative(-0.1)).pack(side="left", padx=2)
        ttk.Button(controls, text="+0.1s", width=5, command=lambda: self._seek_relative(0.1)).pack(side="left")
        ttk.Button(controls, text="+1s", width=4, command=lambda: self._seek_relative(1.0)).pack(side="left", padx=2)

        ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Button(controls, text="⏷  Đặt điểm vào", command=self._set_in_at_cursor).pack(side="left")
        ttk.Button(controls, text="⏶  Đặt điểm ra", command=self._set_out_at_cursor).pack(side="left", padx=4)
        ttk.Button(controls, text="↺  Đặt lại", command=self._reset_range).pack(side="left", padx=4)

        ttk.Label(
            controls,
            text="Phím tắt: I = điểm vào · O = điểm ra · Space = phát/dừng · ←/→ = lùi/tới 0.1s",
            foreground="#666",
        ).pack(side="left", padx=(12, 0))

        # Hàng "Đi đến": nhập giây hoặc HH:MM:SS rồi Enter / bấm "Đi"
        goto = ttk.Frame(left)
        goto.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 4))
        ttk.Label(goto, text="Đi đến (giây hoặc HH:MM:SS):").grid(row=0, column=0, sticky="w")
        self.var_goto = tk.StringVar()
        entry_goto = ttk.Entry(goto, textvariable=self.var_goto, width=14)
        entry_goto.grid(row=0, column=1, sticky="w", padx=6)
        entry_goto.bind("<Return>", lambda _e: self._goto_time())
        ttk.Button(goto, text="Đi", width=4, command=self._goto_time).grid(row=0, column=2, padx=2)
        ttk.Button(goto, text="Đặt vào tại đây", command=self._goto_and_set_in).grid(row=0, column=3, padx=4)
        ttk.Button(goto, text="Đặt ra tại đây", command=self._goto_and_set_out).grid(row=0, column=4, padx=2)
        ttk.Label(
            goto, text="Mẹo: gõ '12.5' hoặc '01:23' rồi Enter",
            foreground="#666",
        ).grid(row=0, column=5, sticky="w", padx=(8, 0))

        # Hiển thị in/out hiện tại + ô chỉnh tay
        cuts = ttk.Frame(left)
        cuts.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 8))
        ttk.Label(cuts, text="Bắt đầu:").grid(row=0, column=0, sticky="w")
        self.var_start = tk.StringVar(value="0")
        ttk.Entry(cuts, textvariable=self.var_start, width=10).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(cuts, text="Kết thúc:").grid(row=0, column=2, sticky="w")
        self.var_end = tk.StringVar(value="")
        ttk.Entry(cuts, textvariable=self.var_end, width=10).grid(row=0, column=3, sticky="w", padx=6)
        self.var_clip_info = tk.StringVar(value="Đoạn được chọn: – ")
        ttk.Label(cuts, textvariable=self.var_clip_info, foreground="#0a6b1f").grid(
            row=0, column=4, sticky="w", padx=(12, 0),
        )
        cuts.columnconfigure(4, weight=1)

        self.var_start.trace_add("write", self._on_text_range_changed)
        self.var_end.trace_add("write", self._on_text_range_changed)

        # --- Cột phải: cấu hình GIF --------------------------------------
        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        right.columnconfigure(0, weight=1)

        # Preset
        preset_frame = ttk.LabelFrame(right, text="Mẫu cài sẵn (preset)")
        preset_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        preset_frame.columnconfigure(1, weight=1)

        ttk.Label(preset_frame, text="Nền tảng:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self._preset_label_to_key = {label: key for key, label in PRESET_OPTIONS}
        self._preset_key_to_label = {key: label for key, label in PRESET_OPTIONS}
        self.var_preset_label = tk.StringVar(value=self._preset_key_to_label["custom"])
        cb = ttk.Combobox(
            preset_frame,
            values=[label for _, label in PRESET_OPTIONS],
            textvariable=self.var_preset_label,
            state="readonly",
        )
        cb.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        cb.bind("<<ComboboxSelected>>", self._on_preset_changed)

        self.lbl_preset_info = ttk.Label(preset_frame, text="", foreground="#555")
        self.lbl_preset_info.grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        # Kích thước
        size_frame = ttk.LabelFrame(right, text="Kích thước & tỉ lệ")
        size_frame.grid(row=1, column=0, sticky="ew", pady=6)
        for c in range(6):
            size_frame.columnconfigure(c, weight=1 if c in (1, 3, 5) else 0)

        ttk.Label(size_frame, text="Rộng:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.var_width = tk.StringVar()
        ttk.Entry(size_frame, textvariable=self.var_width, width=8).grid(row=0, column=1, sticky="w")

        ttk.Label(size_frame, text="Cao:").grid(row=0, column=2, sticky="w")
        self.var_height = tk.StringVar()
        ttk.Entry(size_frame, textvariable=self.var_height, width=8).grid(row=0, column=3, sticky="w")

        ttk.Label(size_frame, text="Cách co:").grid(row=0, column=4, sticky="w")
        self.var_fit_label = tk.StringVar(value=FIT_OPTIONS[0][1])
        self._fit_label_to_key = {label: key for key, label in FIT_OPTIONS}
        ttk.Combobox(
            size_frame,
            values=[label for _, label in FIT_OPTIONS],
            textvariable=self.var_fit_label,
            state="readonly",
            width=22,
        ).grid(row=0, column=5, sticky="w", padx=6)

        ttk.Label(size_frame, text="Màu nền:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.var_background = tk.StringVar(value="#000000")
        ttk.Entry(size_frame, textvariable=self.var_background, width=12).grid(row=1, column=1, sticky="w")
        ttk.Label(
            size_frame,
            text="Mã hex, tên màu, hoặc 'transparent'",
            foreground="#666",
        ).grid(row=1, column=2, columnspan=4, sticky="w")

        # Chất lượng / FPS
        quality_frame = ttk.LabelFrame(right, text="Chất lượng & tốc độ khung hình")
        quality_frame.grid(row=2, column=0, sticky="ew", pady=6)
        for c in range(6):
            quality_frame.columnconfigure(c, weight=1 if c in (1, 3, 5) else 0)

        ttk.Label(quality_frame, text="FPS:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.var_fps = tk.StringVar(value="15")
        ttk.Entry(quality_frame, textvariable=self.var_fps, width=8).grid(row=0, column=1, sticky="w")

        ttk.Label(quality_frame, text="Chất lượng (1–100):").grid(row=0, column=2, sticky="w")
        self.var_quality = tk.StringVar(value="85")
        ttk.Entry(quality_frame, textvariable=self.var_quality, width=8).grid(row=0, column=3, sticky="w")

        ttk.Label(quality_frame, text="Số màu (2–256):").grid(row=0, column=4, sticky="w")
        self.var_colors = tk.StringVar(value="")
        ttk.Entry(quality_frame, textvariable=self.var_colors, width=8).grid(row=0, column=5, sticky="w")

        ttk.Label(quality_frame, text="Khử răng cưa:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.var_dither_label = tk.StringVar(value=DITHER_OPTIONS[0][1])
        self._dither_label_to_key = {label: key for key, label in DITHER_OPTIONS}
        ttk.Combobox(
            quality_frame,
            values=[label for _, label in DITHER_OPTIONS],
            textvariable=self.var_dither_label,
            state="readonly",
            width=22,
        ).grid(row=1, column=1, columnspan=2, sticky="w", padx=(0, 6))

        ttk.Label(quality_frame, text="Dung lượng tối đa (MB):").grid(row=1, column=3, sticky="e")
        self.var_max_mb = tk.StringVar(value="")
        ttk.Entry(quality_frame, textvariable=self.var_max_mb, width=8).grid(row=1, column=4, sticky="w")

        ttk.Label(quality_frame, text="Lặp lại (0=∞):").grid(row=1, column=5, sticky="e")
        self.var_loop = tk.StringVar(value="0")
        ttk.Entry(quality_frame, textvariable=self.var_loop, width=6).grid(row=1, column=6, sticky="w")
        quality_frame.columnconfigure(6, weight=0)

        # Bảng màu
        ttk.Label(quality_frame, text="Bảng màu:").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self._palette_label_to_key = {
            "Mỗi frame một bảng (mặc định, sát màu nhất)": "per_frame",
            "Bảng dùng chung cả clip (đỡ nhấp nháy, file nhỏ)": "global",
        }
        self.var_palette_label = tk.StringVar(
            value="Mỗi frame một bảng (mặc định, sát màu nhất)"
        )
        ttk.Combobox(
            quality_frame,
            values=list(self._palette_label_to_key.keys()),
            textvariable=self.var_palette_label,
            state="readonly",
            width=46,
        ).grid(row=2, column=1, columnspan=6, sticky="w", padx=(0, 6))

        # Cắt và tốc độ
        trim_frame = ttk.LabelFrame(right, text="Tốc độ & giới hạn")
        trim_frame.grid(row=3, column=0, sticky="ew", pady=6)

        ttk.Label(trim_frame, text="Tốc độ phát:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.var_speed = tk.StringVar(value="1.0")
        ttk.Entry(trim_frame, textvariable=self.var_speed, width=8).grid(row=0, column=1, sticky="w")

        ttk.Label(trim_frame, text="Giới hạn frame:").grid(row=0, column=2, sticky="w", padx=6)
        self.var_max_frames = tk.StringVar(value="900")
        ttk.Entry(trim_frame, textvariable=self.var_max_frames, width=8).grid(row=0, column=3, sticky="w")

        self.var_optimize = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            trim_frame, text="Tự động tối ưu để vừa dung lượng",
            variable=self.var_optimize,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=4)

        self.var_flatten_alpha = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            trim_frame, text="Bỏ alpha, nền phẳng",
            variable=self.var_flatten_alpha,
        ).grid(row=1, column=2, columnspan=2, sticky="w", padx=6)

        # Action + progress
        action = ttk.Frame(right)
        action.grid(row=4, column=0, sticky="ew", pady=(8, 4))

        self.btn_run = ttk.Button(action, text="▶  Tạo GIF", command=self._on_run)
        self.btn_run.pack(side="left", padx=2)
        self.btn_open = ttk.Button(action, text="📁  Mở thư mục kết quả", command=self._open_output_folder, state="disabled")
        self.btn_open.pack(side="left", padx=6)
        ttk.Button(action, text="↺  Đặt lại form", command=self._reset_form).pack(side="right", padx=2)

        self.progress = ttk.Progressbar(right, mode="determinate")
        self.progress.grid(row=5, column=0, sticky="ew", pady=(2, 0))

        self.var_status = tk.StringVar(value="Sẵn sàng")
        ttk.Label(right, textvariable=self.var_status).grid(row=6, column=0, sticky="ew", pady=(2, 6))

        # Log
        log_frame = ttk.LabelFrame(root, text="Nhật ký")
        log_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        root.rowconfigure(2, weight=0)
        self.txt_log = tk.Text(log_frame, height=8, wrap="word")
        self.txt_log.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.txt_log.configure(state="disabled")

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------

    def _enable_dnd(self) -> None:
        if not DND_AVAILABLE:
            return
        for widget in (self, self.entry_input, self.preview_canvas, self.lbl_dnd):
            try:
                widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
                widget.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            except tk.TclError:
                pass

    def _on_drop(self, event) -> None:  # type: ignore[no-untyped-def]
        raw = event.data or ""
        # tkdnd trả về danh sách kiểu Tcl; tách an toàn bằng splitlist của Tk.
        try:
            items = list(self.tk.splitlist(raw))
        except tk.TclError:
            items = [raw]
        if not items:
            return
        first = items[0].strip().strip("{}")
        if first:
            self.var_input.set(first)

    # ------------------------------------------------------------------
    # Phím tắt
    # ------------------------------------------------------------------

    def _bind_shortcuts(self) -> None:
        # Bỏ qua khi tiêu điểm đang ở ô nhập text để không vô tình kích hoạt.
        def guarded(handler):
            def wrapper(event):  # type: ignore[no-untyped-def]
                w = self.focus_get()
                if isinstance(w, (tk.Entry, ttk.Entry, tk.Text)):
                    return None
                handler()
                return "break"
            return wrapper

        self.bind_all("<KeyPress-i>", guarded(self._set_in_at_cursor))
        self.bind_all("<KeyPress-I>", guarded(self._set_in_at_cursor))
        self.bind_all("<KeyPress-o>", guarded(self._set_out_at_cursor))
        self.bind_all("<KeyPress-O>", guarded(self._set_out_at_cursor))
        self.bind_all("<KeyPress-space>", guarded(self._toggle_play))
        self.bind_all("<KeyPress-Left>", guarded(lambda: self._seek_relative(-0.1)))
        self.bind_all("<KeyPress-Right>", guarded(lambda: self._seek_relative(0.1)))
        self.bind_all("<Shift-KeyPress-Left>", guarded(lambda: self._seek_relative(-1.0)))
        self.bind_all("<Shift-KeyPress-Right>", guarded(lambda: self._seek_relative(1.0)))

    # ------------------------------------------------------------------
    # File pickers
    # ------------------------------------------------------------------

    def _pick_input_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Chọn tệp đầu vào",
            filetypes=[
                ("Đa phương tiện", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.gif *.png *.jpg *.jpeg *.bmp *.webp"),
                ("Video", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mpg *.mpeg *.3gp *.wmv"),
                ("Hình ảnh", "*.png *.jpg *.jpeg *.bmp *.webp *.gif *.tif *.tiff"),
                ("Tất cả", "*.*"),
            ],
        )
        if path:
            self.var_input.set(path)

    def _pick_input_folder(self) -> None:
        folder = filedialog.askdirectory(title="Chọn thư mục chuỗi ảnh")
        if folder:
            self.var_input.set(folder)

    def _clear_input(self) -> None:
        """Xoá tệp đang chọn để có thể chọn video khác."""
        self._stop_play()
        self.var_input.set("")
        # Cũng xoá output để lần chọn tiếp theo tự sinh đường dẫn mới
        self.var_output.set("")
        # Trạng thái media + nút mở thư mục
        self.btn_open.configure(state="disabled")
        self.var_status.set("Sẵn sàng")
        self.progress.configure(value=0)

    def _pick_output(self) -> None:
        initial = self.var_output.get() or ""
        path = filedialog.asksaveasfilename(
            title="Lưu GIF",
            defaultextension=".gif",
            initialfile=Path(initial).name if initial else "output.gif",
            filetypes=[("GIF", "*.gif")],
        )
        if path:
            self.var_output.set(path)

    # ------------------------------------------------------------------
    # Input changed -> open media + preview
    # ------------------------------------------------------------------

    def _on_input_changed(self, *_args) -> None:
        path_text = self.var_input.get().strip()
        if not self.var_output.get().strip() and path_text:
            try:
                self.var_output.set(str(default_output_path(Path(path_text).expanduser())))
            except Exception:
                pass

        self._stop_play()
        if self._media is not None:
            self._media.close()
            self._media = None

        if not path_text:
            self._draw_preview_placeholder("Chưa có tệp nào – hãy chọn hoặc kéo thả vào.")
            self.var_media_info.set("")
            self.var_time_text.set("00:00.00 / 00:00.00")
            self.range_slider.set_bounds(0.0, 1.0)
            self.var_clip_info.set("Đoạn được chọn: –")
            return

        path = Path(path_text).expanduser()
        if not path.exists():
            self._draw_preview_placeholder("Không tìm thấy tệp.")
            return

        try:
            self._media = MediaSource(path.resolve())
        except Exception as exc:
            self._draw_preview_placeholder(f"Lỗi mở tệp: {exc}")
            self.var_media_info.set("")
            return

        kind_label = {
            "video": "Video",
            "gif": "GIF động",
            "image": "Ảnh",
            "sequence": "Chuỗi ảnh",
        }.get(self._media.kind, self._media.kind)

        info = (
            f"{kind_label} · {self._media.width}×{self._media.height} · "
            f"{self._media.fps:.2f} fps · {self._media.duration:.2f}s · "
            f"{self._media.frame_count} frame"
        )
        self.var_media_info.set(info)

        duration = max(self._media.duration, 1e-3)
        self.range_slider.set_bounds(0.0, duration)
        self.range_slider.set_range(0.0, duration)
        self._cursor_time = 0.0
        self.range_slider.set_cursor(0.0)

        self.var_start.set("0")
        self.var_end.set(f"{duration:.2f}")
        self._update_clip_info()
        self._show_frame_at(0.0)
        self._update_time_label()

    # ------------------------------------------------------------------
    # Preview rendering
    # ------------------------------------------------------------------

    def _draw_preview_placeholder(self, text: str) -> None:
        self.preview_canvas.delete("all")
        self.preview_canvas.create_rectangle(
            0, 0, self.PREVIEW_W, self.PREVIEW_H, fill="#101010", outline="",
        )
        self.preview_canvas.create_text(
            self.PREVIEW_W // 2, self.PREVIEW_H // 2,
            text=text, fill="#cccccc", font=("Segoe UI", 11),
        )

    def _show_frame_at(self, t: float) -> None:
        if self._media is None:
            return
        try:
            frame = self._media.frame_at(t)
        except Exception as exc:
            self._draw_preview_placeholder(f"Lỗi đọc frame: {exc}")
            return
        if frame is None:
            return
        img = frame.copy()
        img.thumbnail((self.PREVIEW_W, self.PREVIEW_H), Image.Resampling.LANCZOS)
        canvas_img = Image.new("RGB", (self.PREVIEW_W, self.PREVIEW_H), (16, 16, 16))
        ox = (self.PREVIEW_W - img.width) // 2
        oy = (self.PREVIEW_H - img.height) // 2
        canvas_img.paste(img.convert("RGB"), (ox, oy))
        self._preview_imgtk = ImageTk.PhotoImage(canvas_img)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(0, 0, anchor="nw", image=self._preview_imgtk)

    def _update_time_label(self) -> None:
        total = self._media.duration if self._media else 0.0
        self.var_time_text.set(f"{_format_time(self._cursor_time)} / {_format_time(total)}")

    def _on_cursor_changed(self, t: float) -> None:
        self._cursor_time = t
        self._show_frame_at(t)
        self._update_time_label()

    # ------------------------------------------------------------------
    # Range editing
    # ------------------------------------------------------------------

    def _on_range_changed(self, lo: float, hi: float) -> None:
        # Cập nhật vào các ô text mà không gây vòng phản hồi.
        self._suspend_text_trace = True
        try:
            self.var_start.set(f"{lo:.2f}")
            self.var_end.set(f"{hi:.2f}")
        finally:
            self._suspend_text_trace = False
        self._update_clip_info()

    def _on_text_range_changed(self, *_args) -> None:
        if getattr(self, "_suspend_text_trace", False):
            return
        if self._media is None:
            self._update_clip_info()
            return
        try:
            lo = parse_timecode(self.var_start.get() or "0")
            end_text = self.var_end.get().strip()
            hi = parse_timecode(end_text) if end_text else self._media.duration
        except Exception:
            return
        lo = max(0.0, min(lo, self._media.duration))
        hi = max(lo + 1e-3, min(hi, self._media.duration))
        self.range_slider.set_range(lo, hi)
        self._update_clip_info()

    def _update_clip_info(self) -> None:
        if self._media is None:
            self.var_clip_info.set("Đoạn được chọn: –")
            return
        lo, hi = self.range_slider.get_range()
        try:
            fps = float(self.var_fps.get() or 15.0)
        except ValueError:
            fps = 15.0
        try:
            speed = float(self.var_speed.get() or 1.0)
        except ValueError:
            speed = 1.0
        try:
            max_frames = int(self.var_max_frames.get() or 900)
        except ValueError:
            max_frames = 900

        duration = max(0.0, hi - lo)
        # Số frame ước lượng = duration * fps / speed (giới hạn bởi max_frames)
        est_frames = int(round(duration * fps / max(speed, 1e-3))) if speed > 0 else 0
        capped = min(est_frames, max_frames)
        self.var_clip_info.set(
            f"Đoạn: {_format_time(lo)} → {_format_time(hi)}  "
            f"({duration:.2f}s, ≈ {capped} frame)"
        )

    def _reset_range(self) -> None:
        if self._media is None:
            return
        self.range_slider.set_range(0.0, self._media.duration)
        self.var_start.set("0")
        self.var_end.set(f"{self._media.duration:.2f}")
        self._update_clip_info()

    def _set_in_at_cursor(self) -> None:
        if self._media is None:
            self.var_status.set("Hãy chọn một tệp trước.")
            return
        lo, hi = self.range_slider.get_range()
        new_lo = min(self._cursor_time, hi - 1e-3)
        self.range_slider.set_range(new_lo, hi)
        self.var_start.set(f"{new_lo:.2f}")
        self._update_clip_info()
        self.var_status.set(f"Đã đặt điểm vào tại {_format_time(new_lo)}.")

    def _set_out_at_cursor(self) -> None:
        if self._media is None:
            self.var_status.set("Hãy chọn một tệp trước.")
            return
        lo, hi = self.range_slider.get_range()
        new_hi = max(self._cursor_time, lo + 1e-3)
        self.range_slider.set_range(lo, new_hi)
        self.var_end.set(f"{new_hi:.2f}")
        self._update_clip_info()
        self.var_status.set(f"Đã đặt điểm ra tại {_format_time(new_hi)}.")

    def _seek_to(self, t: float) -> None:
        if self._media is None:
            return
        t = max(0.0, min(t, self._media.duration))
        self._cursor_time = t
        self.range_slider.set_cursor(t)
        self._show_frame_at(t)
        self._update_time_label()

    def _seek_relative(self, delta: float) -> None:
        if self._media is None:
            return
        self._stop_play()
        self._seek_to(self._cursor_time + delta)

    def _goto_time(self) -> Optional[float]:
        """Đọc giá trị từ ô 'Đi đến' rồi tua tới đó. Trả về thời điểm đã tua."""
        if self._media is None:
            self.var_status.set("Hãy chọn một tệp trước khi 'Đi đến'.")
            return None
        text = (self.var_goto.get() or "").strip()
        if not text:
            self.var_status.set("Nhập số giây hoặc HH:MM:SS để đi đến.")
            return None
        try:
            t = parse_timecode(text)
        except Exception as exc:
            self.var_status.set(f"Giá trị 'Đi đến' không hợp lệ: {exc}")
            return None
        clamped = False
        if t > self._media.duration:
            t = self._media.duration
            clamped = True
        self._stop_play()
        self._seek_to(t)
        if clamped:
            self.var_status.set(
                f"Vượt quá độ dài, đã tua tới cuối ({_format_time(t)})."
            )
        else:
            self.var_status.set(f"Đã đi đến {_format_time(t)}.")
        return self._cursor_time

    def _goto_and_set_in(self) -> None:
        if self._goto_time() is not None:
            self._set_in_at_cursor()

    def _goto_and_set_out(self) -> None:
        if self._goto_time() is not None:
            self._set_out_at_cursor()

    # ------------------------------------------------------------------
    # Play / pause
    # ------------------------------------------------------------------

    def _toggle_play(self) -> None:
        if self._media is None:
            return
        if self._playing:
            self._stop_play()
        else:
            self._start_play()

    def _start_play(self) -> None:
        if self._media is None or self._media.duration <= 0:
            return
        self._playing = True
        self.btn_play.configure(text="⏸  Dừng")
        # phát quanh khoảng đã chọn để xem cho dễ
        lo, hi = self.range_slider.get_range()
        if not (lo <= self._cursor_time < hi):
            self._cursor_time = lo
        self._tick_play()

    def _stop_play(self) -> None:
        self._playing = False
        if self._play_after_id is not None:
            try:
                self.after_cancel(self._play_after_id)
            except Exception:
                pass
            self._play_after_id = None
        self.btn_play.configure(text="▶  Phát")

    def _tick_play(self) -> None:
        if not self._playing or self._media is None:
            return
        lo, hi = self.range_slider.get_range()
        fps = max(self._media.fps, 1.0)
        # Giới hạn để xem trước không quá nặng máy
        preview_fps = min(fps, 24.0)
        step = 1.0 / preview_fps
        self._cursor_time += step
        if self._cursor_time >= hi:
            self._cursor_time = lo  # lặp lại đoạn đang chọn
        self.range_slider.set_cursor(self._cursor_time)
        self._show_frame_at(self._cursor_time)
        self._update_time_label()
        delay_ms = int(1000.0 / preview_fps)
        self._play_after_id = self.after(delay_ms, self._tick_play)

    # ------------------------------------------------------------------
    # Preset / form helpers
    # ------------------------------------------------------------------

    def _on_preset_changed(self, *_args) -> None:
        key = self._preset_label_to_key.get(self.var_preset_label.get(), "custom")
        if key == "custom" or key not in PRESETS:
            self._update_preset_label()
            return
        p = PRESETS[key]
        self.var_width.set(str(p.width))
        self.var_height.set(str(p.height))
        self.var_fps.set(f"{p.fps:g}")
        self.var_quality.set(str(p.quality))
        self.var_colors.set(str(p.colors))
        self.var_max_mb.set(f"{p.max_size_mb:g}" if p.max_size_mb else "")
        self._update_preset_label()
        self._update_clip_info()

    def _update_preset_label(self) -> None:
        key = self._preset_label_to_key.get(self.var_preset_label.get(), "custom")
        if key == "custom" or key not in PRESETS:
            self.lbl_preset_info.configure(text="Tùy chỉnh: thiết lập các trường bên dưới.")
            return
        p = PRESETS[key]
        max_mb = f"{p.max_size_mb:g} MB" if p.max_size_mb else "không giới hạn"
        self.lbl_preset_info.configure(
            text=(
                f"{p.width}×{p.height} · {p.fps:g} fps · chất lượng {p.quality} · "
                f"{p.colors} màu · mục tiêu {max_mb}"
            )
        )

    def _reset_form(self) -> None:
        self._stop_play()
        self.var_input.set("")
        self.var_output.set("")
        self.var_preset_label.set(self._preset_key_to_label["custom"])
        self.var_width.set("")
        self.var_height.set("")
        self.var_fit_label.set(FIT_OPTIONS[0][1])
        self.var_background.set("#000000")
        self.var_fps.set("15")
        self.var_quality.set("85")
        self.var_colors.set("")
        self.var_dither_label.set(DITHER_OPTIONS[0][1])
        self.var_palette_label.set("Mỗi frame một bảng (mặc định, sát màu nhất)")
        self.var_max_mb.set("")
        self.var_loop.set("0")
        self.var_start.set("0")
        self.var_end.set("")
        self.var_speed.set("1.0")
        self.var_max_frames.set("900")
        self.var_optimize.set(True)
        self.var_flatten_alpha.set(False)
        self.var_goto.set("")
        self.progress.configure(value=0)
        self.var_status.set("Sẵn sàng")
        self.btn_open.configure(state="disabled")
        self._update_preset_label()

    def _open_output_folder(self) -> None:
        out = self.var_output.get().strip()
        if not out:
            return
        folder = str(Path(out).expanduser().resolve().parent)
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, f"Không mở được thư mục: {exc}")

    # ------------------------------------------------------------------
    # Build pipeline
    # ------------------------------------------------------------------

    def _build_config(self) -> BuildConfig:
        in_text = self.var_input.get().strip()
        if not in_text:
            raise ValueError("Vui lòng chọn tệp hoặc thư mục đầu vào.")
        input_path = Path(in_text).expanduser().resolve()
        if not input_path.exists():
            raise ValueError(f"Không tìm thấy tệp: {input_path}")

        out_text = self.var_output.get().strip()
        output_path = (
            Path(out_text).expanduser().resolve()
            if out_text
            else default_output_path(input_path)
        )

        preset_key = self._preset_label_to_key.get(self.var_preset_label.get(), "custom")
        preset = PRESETS.get(preset_key) if preset_key != "custom" else None

        def _opt_int(value: str) -> Optional[int]:
            value = value.strip()
            return int(value) if value else None

        def _opt_float(value: str) -> Optional[float]:
            value = value.strip()
            return float(value) if value else None

        try:
            width = _opt_int(self.var_width.get()) or (preset.width if preset else None)
            height = _opt_int(self.var_height.get()) or (preset.height if preset else None)

            fps = _opt_float(self.var_fps.get()) or (preset.fps if preset else 15.0)
            if fps <= 0:
                raise ValueError("FPS phải > 0.")

            quality_raw = _opt_int(self.var_quality.get())
            quality = quality_raw if quality_raw is not None else (preset.quality if preset else 80)
            if not 1 <= quality <= 100:
                raise ValueError("Chất lượng phải trong khoảng 1–100.")

            colors_raw = _opt_int(self.var_colors.get())
            if colors_raw is not None:
                if not 2 <= colors_raw <= 256:
                    raise ValueError("Số màu phải trong khoảng 2–256.")
                colors = colors_raw
            elif preset:
                colors = preset.colors
            else:
                colors = quality_to_colors(quality)

            max_mb = _opt_float(self.var_max_mb.get())
            if max_mb is None and preset:
                max_mb = preset.max_size_mb
            if max_mb is not None and max_mb <= 0:
                raise ValueError("Dung lượng tối đa phải > 0.")

            loop_raw = _opt_int(self.var_loop.get())
            loop = loop_raw if loop_raw is not None else 0
            if loop < 0:
                raise ValueError("Số lần lặp phải >= 0.")

            max_frames_raw = _opt_int(self.var_max_frames.get())
            max_frames = max_frames_raw if max_frames_raw is not None else 900
            if max_frames <= 0:
                raise ValueError("Giới hạn frame phải > 0.")

            speed_raw = _opt_float(self.var_speed.get())
            speed = speed_raw if speed_raw is not None else 1.0
            if speed <= 0:
                raise ValueError("Tốc độ phải > 0.")

            start = parse_timecode(self.var_start.get() or "0")
            end_text = self.var_end.get().strip()
            end = parse_timecode(end_text) if end_text else None
            if end is not None and end <= start:
                raise ValueError("Điểm kết thúc phải lớn hơn điểm bắt đầu.")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Giá trị không hợp lệ: {exc}")

        background = parse_background(self.var_background.get() or "#000000")
        keep_alpha = not self.var_flatten_alpha.get()
        if not keep_alpha and background is None:
            background = (0, 0, 0, 255)

        from PIL import Image as _Img
        dither_value = (
            _Img.Dither.FLOYDSTEINBERG
            if self._dither_label_to_key.get(self.var_dither_label.get()) == "floyd"
            else _Img.Dither.NONE
        )

        fit_key = self._fit_label_to_key.get(self.var_fit_label.get(), "contain")
        palette_key = self._palette_label_to_key.get(self.var_palette_label.get(), "per_frame")

        return BuildConfig(
            input_path=input_path,
            output_path=output_path,
            width=width,
            height=height,
            fit=fit_key,
            background=background,
            fps=fps,
            quality=quality,
            colors=colors,
            dither=dither_value,
            start=start,
            end=end,
            duration=None,
            speed=speed,
            loop=loop,
            max_size_mb=max_mb,
            max_frames=max_frames,
            optimize=self.var_optimize.get(),
            keep_alpha=keep_alpha,
            palette_mode=palette_key,
        )

    def _on_run(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        try:
            cfg = self._build_config()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self._stop_play()
        self.btn_run.configure(state="disabled")
        self.btn_open.configure(state="disabled")
        self.progress.configure(value=0)
        self._log_clear()
        self._log(f"Đang tạo GIF: {cfg.output_path}")
        self.var_status.set("Đang xử lý…")

        def worker() -> None:
            try:
                result = build_gif(cfg, on_progress=self._on_progress)
                self._queue.put(("done", cfg, result))
            except Exception:
                self._queue.put(("error", traceback.format_exc()))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_progress(self, stage: str, fraction: float, message: str) -> None:
        self._queue.put(("progress", stage, fraction, message))

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]

                if kind == "progress":
                    _, stage, fraction, message = item
                    self.progress.configure(value=max(0, min(100, fraction * 100)))
                    self.var_status.set(
                        f"[{_translate_stage(stage)}] {_translate_message(message)}"
                    )

                elif kind == "done":
                    _, cfg, result = item
                    self.progress.configure(value=100)
                    self._handle_done(cfg, result)
                    self.btn_run.configure(state="normal")

                elif kind == "error":
                    _, tb = item
                    self.var_status.set("Thất bại")
                    self._log(tb)
                    last = tb.strip().splitlines()[-1] if tb.strip() else "Lỗi không xác định"
                    messagebox.showerror(APP_TITLE, last)
                    self.btn_run.configure(state="normal")
        except queue.Empty:
            pass
        finally:
            self.after(80, self._drain_queue)

    def _handle_done(self, cfg: BuildConfig, result: BuildResult) -> None:
        self.var_status.set(
            f"Hoàn tất · {result.frames} frame · {result.fps:.2f} fps · {result.file_size_mb:.2f} MB"
        )
        self._log("=" * 60)
        self._log(f"Tệp ra      : {result.output_path}")
        self._log(f"Kích thước  : {result.width}×{result.height}")
        self._log(f"Số frame    : {result.frames}")
        self._log(f"FPS         : {result.fps:.2f}")
        self._log(f"Thời lượng  : {result.duration_seconds:.2f}s")
        self._log(f"Số màu      : tối đa {result.colors_used}")
        self._log(f"Lặp         : {'vô hạn' if cfg.loop == 0 else cfg.loop}")
        if result.target_size_mb:
            self._log(f"Mục tiêu MB : {result.target_size_mb:g}")
        self._log(f"Dung lượng  : {result.file_size_mb:.2f} MB")
        self.btn_open.configure(state="normal")

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log(self, text: str) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _log_clear(self) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")


# ===========================================================================
# Helpers
# ===========================================================================


def _format_time(t: float) -> str:
    if t < 0:
        t = 0.0
    minutes = int(t // 60)
    seconds = t - minutes * 60
    return f"{minutes:02d}:{seconds:05.2f}"


def _translate_stage(stage: str) -> str:
    return {
        "decode": "Đọc khung hình",
        "quantize": "Giảm bảng màu",
        "encode": "Ghi GIF",
        "optimize": "Tối ưu",
        "done": "Xong",
    }.get(stage, stage)


def _translate_message(message: str) -> str:
    """Dịch sang tiếng Việt một số chuỗi message của core library."""
    if not message:
        return message
    replacements = [
        ("Building global palette...", "Đang dựng bảng màu chung…"),
        ("Apply palette", "Áp bảng màu"),
        ("Quantize ", "Giảm màu "),
        ("Reading input", "Đọc nguồn"),
        ("Decoded", "Đã đọc"),
        ("frames", "frame"),
        ("Loaded single image", "Đã tải ảnh"),
        ("Quantizing colors", "Đang giảm màu"),
        ("Writing GIF", "Đang ghi GIF"),
        ("Finished", "Hoàn tất"),
        ("Optimize round", "Vòng tối ưu"),
        ("Round", "Vòng"),
        ("colors=", "số màu="),
        ("size=", "khổ="),
        ("target", "mục tiêu"),
    ]
    out = message
    for src, dst in replacements:
        out = out.replace(src, dst)
    return out


def launch() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    launch()
