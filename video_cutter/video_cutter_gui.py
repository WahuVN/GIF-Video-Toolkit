#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Giao diện đồ họa cho công cụ cắt video.

Dùng chung với GIF Tool: tái sử dụng `MediaSource` và `RangeSlider` từ
`gif_gui.py` ở thư mục cha để có cùng cách xem trước, kéo thanh thời gian,
và đặt điểm vào / điểm ra.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Cho phép import gif_gui từ thư mục cha (D:\TOOL\Tao GIF)
_THIS = Path(__file__).resolve()
_PARENT = _THIS.parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from gif_gui import MediaSource, RangeSlider  # type: ignore  # noqa: E402

from video_cutter import (  # noqa: E402
    CutOptions,
    cut_video,
    format_time,
    parse_timecode,
    probe,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore

    DND_AVAILABLE = True
except ImportError:  # pragma: no cover
    DND_AVAILABLE = False
    DND_FILES = None  # type: ignore
    TkinterDnD = None  # type: ignore

from PIL import Image, ImageTk

APP_TITLE = "Công cụ cắt video"

PRESET_DURATIONS = [
    ("Tự nhập", None),
    ("5 giây", 5.0),
    ("10 giây", 10.0),
    ("15 giây", 15.0),
    ("30 giây", 30.0),
    ("1 phút", 60.0),
    ("3 phút", 180.0),
]


_BaseTk = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk


class CutterApp(_BaseTk):  # type: ignore[misc, valid-type]
    PREVIEW_W = 560
    PREVIEW_H = 315

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x780")
        self.minsize(960, 700)

        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._media: Optional[MediaSource] = None
        self._preview_imgtk: Optional[ImageTk.PhotoImage] = None

        self._cursor_time = 0.0
        self._playing = False
        self._play_after_id: Optional[str] = None
        self._suspend_text_trace = False

        self._build_styles()
        self._build_widgets()
        self._enable_dnd()
        self._bind_shortcuts()

        self.after(80, self._drain_queue)

    # ------------------------------------------------------------------
    # Styles + widgets
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
        root.columnconfigure(1, weight=0)
        root.rowconfigure(1, weight=1)

        # I/O
        io = ttk.LabelFrame(root, text="Tệp đầu vào / đầu ra")
        io.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for i in range(5):
            io.columnconfigure(i, weight=1 if i == 1 else 0)

        ttk.Label(io, text="Tệp video:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.var_input = tk.StringVar()
        self.entry_input = ttk.Entry(io, textvariable=self.var_input)
        self.entry_input.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(io, text="✕", width=3, command=self._clear_input).grid(row=0, column=2, padx=(0, 4))
        ttk.Button(io, text="Chọn tệp…", command=self._pick_input_file).grid(row=0, column=3, padx=4)

        ttk.Label(io, text="Lưu tại:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.var_output = tk.StringVar()
        ttk.Entry(io, textvariable=self.var_output).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(io, text="✕", width=3, command=lambda: self.var_output.set("")).grid(row=1, column=2, padx=(0, 4))
        ttk.Button(io, text="Lưu thành…", command=self._pick_output).grid(row=1, column=3, padx=4)

        dnd_text = (
            "Mẹo: kéo thả tệp video vào cửa sổ để mở nhanh."
            if DND_AVAILABLE
            else "Để bật kéo thả: pip install tkinterdnd2"
        )
        self.lbl_dnd = ttk.Label(io, text=dnd_text, foreground="#666")
        self.lbl_dnd.grid(row=2, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 4))

        self.var_input.trace_add("write", self._on_input_changed)

        # Preview + range (cột trái)
        left = ttk.LabelFrame(root, text="Xem trước & chọn đoạn")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(
            left, width=self.PREVIEW_W, height=self.PREVIEW_H,
            bg="#101010", highlightthickness=0,
        )
        self.preview_canvas.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._draw_preview_placeholder("Chưa có tệp – chọn hoặc kéo thả vào.")

        info_row = ttk.Frame(left)
        info_row.grid(row=1, column=0, sticky="ew", padx=8)
        self.var_time_text = tk.StringVar(value="00:00.000 / 00:00.000")
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
            text="Phím tắt: I = vào · O = ra · Space = phát/dừng · ←/→ = lùi/tới 0.1s",
            foreground="#666",
        ).pack(side="left", padx=(12, 0))

        # Hàng "Đi đến"
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

        # Hàng start/end + clip info
        cuts = ttk.Frame(left)
        cuts.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 8))
        ttk.Label(cuts, text="Bắt đầu:").grid(row=0, column=0, sticky="w")
        self.var_start = tk.StringVar(value="0")
        ttk.Entry(cuts, textvariable=self.var_start, width=12).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(cuts, text="Kết thúc:").grid(row=0, column=2, sticky="w")
        self.var_end = tk.StringVar(value="")
        ttk.Entry(cuts, textvariable=self.var_end, width=12).grid(row=0, column=3, sticky="w", padx=6)
        self.var_clip_info = tk.StringVar(value="Đoạn được chọn: –")
        ttk.Label(cuts, textvariable=self.var_clip_info, foreground="#0a6b1f").grid(
            row=0, column=4, sticky="w", padx=(12, 0),
        )
        cuts.columnconfigure(4, weight=1)

        self.var_start.trace_add("write", self._on_text_range_changed)
        self.var_end.trace_add("write", self._on_text_range_changed)

        # Cài đặt cắt (cột phải)
        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        # Đoạn cài sẵn
        preset_frame = ttk.LabelFrame(right, text="Độ dài cài sẵn")
        preset_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        preset_frame.columnconfigure(0, weight=1)
        ttk.Label(preset_frame, text="Đặt nhanh độ dài đoạn (tính từ điểm vào):").grid(
            row=0, column=0, sticky="w", padx=6, pady=4,
        )
        btns = ttk.Frame(preset_frame)
        btns.grid(row=1, column=0, sticky="w", padx=6, pady=(0, 6))
        for label, secs in PRESET_DURATIONS:
            if secs is None:
                continue
            ttk.Button(
                btns, text=label, width=8,
                command=lambda s=secs: self._apply_duration_preset(s),
            ).pack(side="left", padx=2)

        # Chế độ cắt
        mode_frame = ttk.LabelFrame(right, text="Chế độ cắt")
        mode_frame.grid(row=1, column=0, sticky="ew", pady=6)
        self.var_mode = tk.StringVar(value="copy")
        ttk.Radiobutton(
            mode_frame, text="Sao chép luồng (siêu nhanh, không mất chất lượng – có thể lệch keyframe)",
            variable=self.var_mode, value="copy",
            command=self._on_mode_changed,
        ).grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Radiobutton(
            mode_frame, text="Tái mã hóa H.264 (cắt chính xác từng frame, lâu hơn)",
            variable=self.var_mode, value="precise",
            command=self._on_mode_changed,
        ).grid(row=1, column=0, sticky="w", padx=6, pady=2)

        # Tham số precise
        self.precise_frame = ttk.LabelFrame(right, text="Tùy chọn tái mã hóa")
        self.precise_frame.grid(row=2, column=0, sticky="ew", pady=6)
        self.precise_frame.columnconfigure(1, weight=1)

        ttk.Label(self.precise_frame, text="CRF (0=lossless · 23 mặc định · 51 tệ):").grid(
            row=0, column=0, sticky="w", padx=6, pady=4,
        )
        self.var_crf = tk.IntVar(value=20)
        ttk.Spinbox(self.precise_frame, from_=0, to=51, textvariable=self.var_crf, width=6).grid(
            row=0, column=1, sticky="w", padx=6,
        )

        ttk.Label(self.precise_frame, text="Tốc độ encode:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.var_preset = tk.StringVar(value="medium")
        ttk.Combobox(
            self.precise_frame,
            values=["ultrafast", "superfast", "veryfast", "faster", "fast",
                    "medium", "slow", "slower", "veryslow"],
            textvariable=self.var_preset, state="readonly", width=14,
        ).grid(row=1, column=1, sticky="w", padx=6)

        # Tùy chọn chung
        opts = ttk.LabelFrame(right, text="Tùy chọn")
        opts.grid(row=3, column=0, sticky="ew", pady=6)
        self.var_keep_audio = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Giữ tiếng", variable=self.var_keep_audio).grid(
            row=0, column=0, sticky="w", padx=6, pady=2,
        )
        self.var_overwrite = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Ghi đè nếu tệp đầu ra đã tồn tại", variable=self.var_overwrite).grid(
            row=1, column=0, sticky="w", padx=6, pady=2,
        )
        self.var_open_after = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Mở thư mục kết quả khi xong", variable=self.var_open_after).grid(
            row=2, column=0, sticky="w", padx=6, pady=2,
        )

        # Hành động + tiến trình
        action = ttk.Frame(right)
        action.grid(row=4, column=0, sticky="ew", pady=(8, 4))
        self.btn_run = ttk.Button(action, text="✂  Cắt video", command=self._on_run)
        self.btn_run.pack(side="left", padx=2)
        self.btn_open = ttk.Button(action, text="📁  Mở thư mục kết quả",
                                   command=self._open_output_folder, state="disabled")
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
        self.txt_log = tk.Text(log_frame, height=7, wrap="word")
        self.txt_log.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.txt_log.configure(state="disabled")

        self._on_mode_changed()

    # ------------------------------------------------------------------
    # DND
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
    # Shortcuts
    # ------------------------------------------------------------------

    def _bind_shortcuts(self) -> None:
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
            title="Chọn video",
            filetypes=[
                ("Video", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mpg *.mpeg *.3gp *.wmv *.flv *.ts"),
                ("Tất cả", "*.*"),
            ],
        )
        if path:
            self.var_input.set(path)

    def _pick_output(self) -> None:
        initial = self.var_output.get() or ""
        path = filedialog.asksaveasfilename(
            title="Lưu video",
            defaultextension=".mp4",
            initialfile=Path(initial).name if initial else "cut.mp4",
            filetypes=[("MP4", "*.mp4"), ("MKV", "*.mkv"), ("Tất cả", "*.*")],
        )
        if path:
            self.var_output.set(path)

    def _clear_input(self) -> None:
        self._stop_play()
        self.var_input.set("")
        self.var_output.set("")
        self.btn_open.configure(state="disabled")
        self.var_status.set("Sẵn sàng")
        self.progress.configure(value=0)

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def _on_input_changed(self, *_args) -> None:
        path_text = self.var_input.get().strip()
        if not self.var_output.get().strip() and path_text:
            try:
                self.var_output.set(self._auto_output(Path(path_text).expanduser()))
            except Exception:
                pass

        self._stop_play()
        if self._media is not None:
            self._media.close()
            self._media = None

        if not path_text:
            self._draw_preview_placeholder("Chưa có tệp – chọn hoặc kéo thả vào.")
            self.var_media_info.set("")
            self.var_time_text.set("00:00.000 / 00:00.000")
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
            return

        if self._media.kind != "video":
            self._draw_preview_placeholder("Tệp không phải video.")
            self._media.close()
            self._media = None
            return

        # Kết hợp ffprobe để có thời lượng chính xác hơn nếu có thể
        duration = self._media.duration
        try:
            info = probe(path.resolve())
            if info.duration > duration:
                duration = info.duration
        except Exception:
            pass

        self.var_media_info.set(
            f"{self._media.width}×{self._media.height} · {self._media.fps:.2f} fps · "
            f"{format_time(duration)} · {self._media.frame_count} frame"
        )

        duration_safe = max(duration, 1e-3)
        self.range_slider.set_bounds(0.0, duration_safe)
        self.range_slider.set_range(0.0, duration_safe)
        self._cursor_time = 0.0
        self.range_slider.set_cursor(0.0)
        self.var_start.set("0")
        self.var_end.set(f"{duration_safe:.3f}")
        self._update_clip_info()
        self._show_frame_at(0.0)
        self._update_time_label()

    def _auto_output(self, input_path: Path) -> str:
        out = input_path.with_name(f"{input_path.stem}_cut{input_path.suffix or '.mp4'}")
        return str(out.resolve())

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
        self.var_time_text.set(f"{format_time(self._cursor_time)} / {format_time(total)}")

    def _on_cursor_changed(self, t: float) -> None:
        self._cursor_time = t
        self._show_frame_at(t)
        self._update_time_label()

    # ------------------------------------------------------------------
    # Range editing
    # ------------------------------------------------------------------

    def _on_range_changed(self, lo: float, hi: float) -> None:
        self._suspend_text_trace = True
        try:
            self.var_start.set(f"{lo:.3f}")
            self.var_end.set(f"{hi:.3f}")
        finally:
            self._suspend_text_trace = False
        self._update_clip_info()

    def _on_text_range_changed(self, *_args) -> None:
        if self._suspend_text_trace or self._media is None:
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
        duration = max(0.0, hi - lo)
        self.var_clip_info.set(
            f"Đoạn: {format_time(lo)} → {format_time(hi)}  ({format_time(duration)})"
        )

    def _reset_range(self) -> None:
        if self._media is None:
            return
        self.range_slider.set_range(0.0, self._media.duration)
        self.var_start.set("0")
        self.var_end.set(f"{self._media.duration:.3f}")
        self._update_clip_info()

    def _set_in_at_cursor(self) -> None:
        if self._media is None:
            self.var_status.set("Hãy chọn một video trước.")
            return
        lo, hi = self.range_slider.get_range()
        new_lo = min(self._cursor_time, hi - 1e-3)
        self.range_slider.set_range(new_lo, hi)
        self.var_start.set(f"{new_lo:.3f}")
        self._update_clip_info()
        self.var_status.set(f"Đã đặt điểm vào tại {format_time(new_lo)}.")

    def _set_out_at_cursor(self) -> None:
        if self._media is None:
            self.var_status.set("Hãy chọn một video trước.")
            return
        lo, hi = self.range_slider.get_range()
        new_hi = max(self._cursor_time, lo + 1e-3)
        self.range_slider.set_range(lo, new_hi)
        self.var_end.set(f"{new_hi:.3f}")
        self._update_clip_info()
        self.var_status.set(f"Đã đặt điểm ra tại {format_time(new_hi)}.")

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
        if self._media is None:
            self.var_status.set("Hãy chọn một video trước.")
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
        self.var_status.set(
            f"{'Vượt độ dài, đã tua tới cuối' if clamped else 'Đã đi đến'} ({format_time(t)})."
        )
        return self._cursor_time

    def _goto_and_set_in(self) -> None:
        if self._goto_time() is not None:
            self._set_in_at_cursor()

    def _goto_and_set_out(self) -> None:
        if self._goto_time() is not None:
            self._set_out_at_cursor()

    def _apply_duration_preset(self, seconds: float) -> None:
        if self._media is None:
            self.var_status.set("Hãy chọn một video trước.")
            return
        try:
            lo = parse_timecode(self.var_start.get() or "0")
        except Exception:
            lo = 0.0
        lo = max(0.0, min(lo, self._media.duration))
        hi = min(self._media.duration, lo + seconds)
        if hi - lo < 1e-3:
            hi = min(self._media.duration, lo + 1.0)
        self.range_slider.set_range(lo, hi)
        self._update_clip_info()
        self.var_status.set(f"Đã đặt độ dài {format_time(seconds)} từ {format_time(lo)}.")

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
        preview_fps = min(fps, 24.0)
        step = 1.0 / preview_fps
        self._cursor_time += step
        if self._cursor_time >= hi:
            self._cursor_time = lo
        self.range_slider.set_cursor(self._cursor_time)
        self._show_frame_at(self._cursor_time)
        self._update_time_label()
        delay_ms = int(1000.0 / preview_fps)
        self._play_after_id = self.after(delay_ms, self._tick_play)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_mode_changed(self) -> None:
        precise = self.var_mode.get() == "precise"
        for child in self.precise_frame.winfo_children():
            try:
                child.configure(state="normal" if precise else "disabled")
            except tk.TclError:
                pass

    def _reset_form(self) -> None:
        self._stop_play()
        self.var_input.set("")
        self.var_output.set("")
        self.var_start.set("0")
        self.var_end.set("")
        self.var_goto.set("")
        self.var_mode.set("copy")
        self.var_keep_audio.set(True)
        self.var_overwrite.set(True)
        self.var_open_after.set(False)
        self.var_crf.set(20)
        self.var_preset.set("medium")
        self.progress.configure(value=0)
        self.var_status.set("Sẵn sàng")
        self.btn_open.configure(state="disabled")
        self._on_mode_changed()

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
    # Run
    # ------------------------------------------------------------------

    def _build_options(self) -> CutOptions:
        in_text = self.var_input.get().strip()
        if not in_text:
            raise ValueError("Vui lòng chọn video đầu vào.")
        input_path = Path(in_text).expanduser().resolve()
        if not input_path.exists():
            raise ValueError(f"Không tìm thấy tệp: {input_path}")

        out_text = self.var_output.get().strip()
        output_path = Path(out_text).expanduser().resolve() if out_text else \
            Path(self._auto_output(input_path)).resolve()

        try:
            start = parse_timecode(self.var_start.get() or "0")
            end_text = self.var_end.get().strip()
            if not end_text:
                raise ValueError("Vui lòng nhập điểm kết thúc.")
            end = parse_timecode(end_text)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Thời gian không hợp lệ: {exc}")

        if end <= start:
            raise ValueError("Điểm kết thúc phải lớn hơn điểm bắt đầu.")

        return CutOptions(
            input_path=input_path,
            output_path=output_path,
            start=start,
            end=end,
            mode=self.var_mode.get(),
            keep_audio=self.var_keep_audio.get(),
            crf=int(self.var_crf.get() or 20),
            preset=self.var_preset.get() or "medium",
            overwrite=self.var_overwrite.get(),
        )

    def _on_run(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        try:
            opts = self._build_options()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        if not opts.overwrite and opts.output_path.exists():
            if not messagebox.askyesno(
                APP_TITLE,
                f"Tệp đã tồn tại:\n{opts.output_path}\nGhi đè?",
            ):
                return
            opts.overwrite = True

        self._stop_play()
        self.btn_run.configure(state="disabled")
        self.btn_open.configure(state="disabled")
        self.progress.configure(value=0)
        self._log_clear()
        self._log(f"Cắt video: {opts.input_path}")
        self._log(f"Đoạn: {format_time(opts.start)} → {format_time(opts.end)} "
                  f"({format_time(opts.end - opts.start)})")
        self._log(f"Đầu ra: {opts.output_path}")
        self._log(f"Chế độ: {opts.mode} · Tiếng: {'có' if opts.keep_audio else 'không'}")
        self.var_status.set("Đang cắt video…")

        def worker() -> None:
            t0 = time.time()
            try:
                cut_video(opts, on_progress=self._on_progress)
                elapsed = time.time() - t0
                self._queue.put(("done", opts, elapsed))
            except Exception:
                self._queue.put(("error", traceback.format_exc()))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_progress(self, fraction: float, message: str) -> None:
        self._queue.put(("progress", fraction, message))

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]

                if kind == "progress":
                    _, fraction, message = item
                    self.progress.configure(value=max(0, min(100, fraction * 100)))
                    self.var_status.set(message)

                elif kind == "done":
                    _, opts, elapsed = item
                    self.progress.configure(value=100)
                    self._handle_done(opts, elapsed)
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

    def _handle_done(self, opts: CutOptions, elapsed: float) -> None:
        size_mb = opts.output_path.stat().st_size / (1024 * 1024)
        self.var_status.set(
            f"Hoàn tất · {format_time(opts.end - opts.start)} · {size_mb:.2f} MB · {elapsed:.2f}s"
        )
        self._log("=" * 60)
        self._log(f"Đầu ra      : {opts.output_path}")
        self._log(f"Đoạn        : {format_time(opts.start)} → {format_time(opts.end)}")
        self._log(f"Thời lượng  : {format_time(opts.end - opts.start)}")
        self._log(f"Dung lượng  : {size_mb:.2f} MB")
        self._log(f"Mất         : {elapsed:.2f}s")
        self.btn_open.configure(state="normal")
        if self.var_open_after.get():
            self._open_output_folder()

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


def launch() -> None:
    app = CutterApp()
    app.mainloop()


if __name__ == "__main__":
    launch()
