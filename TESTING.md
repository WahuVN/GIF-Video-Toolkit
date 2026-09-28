# Testing & Validation — GIF & Video Toolkit

## Automated Test Suites
Run all discovery unit tests:
```powershell
python -m unittest discover -s tests -v
```

## Compilation Syntax Check
```powershell
python -m compileall -q media_runtime.py media_job_queue.py gif_tool.py gif_gui.py video_cutter tests benchmarks
```

## Benchmarks
Run performance benchmarks:
```powershell
python benchmarks\benchmark_core.py
```
