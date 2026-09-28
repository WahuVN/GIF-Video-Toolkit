# Setup Guide — GIF & Video Toolkit

## Prerequisites
- Python 3.10+ (recommended 3.11/3.12)
- FFmpeg on system PATH or local runtime directory

## Installation
```powershell
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

## Running the Tools
- GUI Tool: `python gif_gui.py`
- CLI Tool: `python gif_tool.py --list-presets`
- Video Cutter: `python video_cutter\video_cutter.py --help`
