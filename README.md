# GIF & Video Toolkit

A compact Windows media utility with both GUI and CLI workflows for creating optimized GIFs and cutting video clips.

The project combines two related tools:

- **GIF creator** — convert video, animated GIFs, still images or image sequences into optimized GIF output.
- **Video cutter** — trim clips quickly with stream copy or precisely with H.264/AAC re-encoding.

## GIF creator highlights

- Video/image-sequence input.
- GUI preview with timeline in/out selection.
- Presets for social, chat and web targets.
- `contain`, `cover` and `stretch` fitting modes.
- Configurable FPS, dimensions, palette size, dithering and playback speed.
- Optional output-size target that iteratively reduces GIF weight.
- Transparent or solid backgrounds.
- CLI and Tkinter GUI.
- Sequential multi-job queue with per-job state/progress/cancel.
- Pre-encode size estimate for planning before expensive work.

## Video cutter highlights

- Fast stream-copy mode for lossless cuts around keyframes.
- Precise H.264/AAC mode for frame-accurate trimming.
- Start/end/duration controls.
- Optional audio removal.
- Metadata probe mode.
- Timeline-based desktop GUI and CLI.
- Millisecond timecode plus one-frame stepping in the cutter GUI.
- Real FFmpeg process cancellation with partial-output cleanup.
- Pre-cut size estimate for copy and precise modes.

## Requirements

- Windows 10/11
- Python 3.10+

```powershell
python -m pip install -r requirements.txt
```

Media-tool discovery checks an explicitly configured path, `WAHU_FFMPEG_PATH` / `WAHU_FFPROBE_PATH`, the system `PATH`, and finally the `imageio-ffmpeg` FFmpeg fallback. Candidates are verified with `-version` before use. Release packages must not bundle an FFmpeg binary without an explicit license/provenance review of that exact build.

## Run

GIF GUI:

```powershell
python gif_gui.py
```

GIF CLI example:

```powershell
python gif_tool.py -i input.mp4 --preset discord --max-size-mb 8 -o output.gif
```

List available GIF presets:

```powershell
python gif_tool.py --list-presets
```

Video cutter GUI:

```powershell
python video_cutter\video_cutter_gui.py
```

Video cutter CLI:

```powershell
python video_cutter\video_cutter.py -i input.mp4 --start 10 --duration 15 --mode precise -o clip.mp4
```

Windows launchers are included at the repository root.

## Examples

Vertical social GIF:

```powershell
python gif_tool.py -i clip.mp4 --preset tiktok -o vertical.gif
```

Small web GIF:

```powershell
python gif_tool.py -i clip.mp4 --preset web_small --max-size-mb 2.5 -o preview.gif
```

Fast video cut:

```powershell
python video_cutter\video_cutter.py -i long.mp4 --start 60 --end 90 -o excerpt.mp4
```

## Validation

```powershell
python gif_tool.py --list-presets
python -m unittest discover -s tests -v
python benchmarks/benchmark_core.py
python -m compileall -q media_runtime.py media_job_queue.py gif_tool.py gif_gui.py video_cutter tests benchmarks
```

CI runs preset smoke, focused unit tests, benchmark smoke and compile checks on Windows.

## Project layout

```text
media_runtime.py
media_job_queue.py
gif_tool.py
gif_gui.py
video_cutter/
  video_cutter.py
  video_cutter_gui.py
  requirements.txt
00_MENU_GIF.bat
01_TAO_GIF.bat
02_CAT_VIDEO.bat
03_GIF_CLI.bat
```

## Notes

Large source videos can use significant memory when converted to GIF. Prefer short ranges, sensible frame rates and the size-limit option for shareable output.

---

Vietnamese note: bộ công cụ tạo GIF và cắt video, có GUI/CLI, preset theo nền tảng và chế độ cắt nhanh hoặc chính xác.
