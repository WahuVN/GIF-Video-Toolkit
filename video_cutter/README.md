# Video Cutter

Companion video-trimming utility included in GIF & Video Toolkit.

## Modes

- `copy` — fast stream copy with no re-encoding; cut boundaries follow nearby keyframes.
- `precise` — H.264/AAC re-encode for accurate start/end timing.

## GUI

```powershell
python video_cutter_gui.py
```

The GUI supports preview, timeline seeking, in/out points, quick duration presets and drag-and-drop when `tkinterdnd2` is available.

## CLI

Fast cut:

```powershell
python video_cutter.py -i input.mp4 --start 10 --end 25 -o output.mp4
```

Frame-accurate cut:

```powershell
python video_cutter.py -i input.mp4 --start 10 --duration 15 --mode precise --crf 20 -o output.mp4
```

Remove audio:

```powershell
python video_cutter.py -i input.mp4 --start 0 --end 30 --no-audio -o silent.mp4
```

Probe metadata:

```powershell
python video_cutter.py -i input.mp4 --probe
```

## Dependencies

The root project requirements already include the dependencies needed by this tool, including `imageio-ffmpeg` for a bundled FFmpeg runtime.
