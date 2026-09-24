import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'video_cutter'))
from video_cutter import CutOptions, VideoInfo, _build_cmd, estimate_cut_size, format_time, parse_timecode


class CutterCoreTests(unittest.TestCase):
    def test_timecode_millisecond_roundtrip(self):
        value = parse_timecode('01:02:03.456')
        self.assertAlmostEqual(value, 3723.456, places=6)
        self.assertEqual(format_time(value), '01:02:03.456')

    def test_commands_copy_and_precise(self):
        p = Path('in.mp4')
        copy = _build_cmd(CutOptions(p, Path('out.mp4'), 1.125, 3.875), 'ffmpeg')
        precise = _build_cmd(CutOptions(p, Path('out.mp4'), 1.125, 3.875, mode='precise'), 'ffmpeg')
        self.assertIn('copy', copy)
        self.assertIn('libx264', precise)
        self.assertIn('1.125', precise)
        self.assertIn('2.750', precise)

    def test_copy_estimator(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'in.mp4'
            src.write_bytes(b'x' * 10000)
            opts = CutOptions(src, Path(td) / 'out.mp4', 2, 7)
            info = VideoInfo(10, 1920, 1080, 30, 'h264', 'aac')
            self.assertEqual(estimate_cut_size(opts, info), 5000)


if __name__ == '__main__':
    unittest.main()
