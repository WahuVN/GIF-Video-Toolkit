import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from time import perf_counter
from media_runtime import estimate_gif_bytes, estimate_precise_bytes

N = 100000
start = perf_counter()
for _ in range(N):
    estimate_gif_bytes(640, 360, 120, 128, 'global')
    estimate_precise_bytes(12.5, 20, True, 1920, 1080, 30)
elapsed = perf_counter() - start
print(f'estimator_calls={N*2} elapsed_s={elapsed:.6f} avg_us={(elapsed/(N*2))*1e6:.3f}')
