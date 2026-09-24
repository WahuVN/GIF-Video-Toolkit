import unittest
from pathlib import Path

from gif_tool import BuildConfig, estimate_gif_size
from media_runtime import CancelledError, CancellationToken


class GifCoreTests(unittest.TestCase):
    def test_estimate_respects_duration_and_cap(self):
        cfg = BuildConfig(Path('in.mp4'), Path('out.gif'), width=320, height=240, fps=10, max_frames=50)
        a = estimate_gif_size(cfg, 1920, 1080, 2)
        b = estimate_gif_size(cfg, 1920, 1080, 20)
        self.assertGreaterEqual(b, a)

    def test_drag_drop_wiring_preserved(self):
        root = Path(__file__).resolve().parents[1]
        for rel in ('gif_gui.py', 'video_cutter/video_cutter_gui.py'):
            text = (root / rel).read_text(encoding='utf-8-sig')
            self.assertIn('drop_target_register', text)
            self.assertIn('<<Drop>>', text)
            self.assertIn('_on_drop', text)


if __name__ == '__main__':
    unittest.main()
