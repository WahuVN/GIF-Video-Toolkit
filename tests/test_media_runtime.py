import os
import tempfile
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from media_runtime import (
    CancelledError, CancellationToken, clear_discovery_cache, discover_media_tool,
    estimate_gif_bytes, estimate_precise_bytes, estimate_stream_copy_bytes,
    snap_time_to_frame, terminate_process,
)


class RuntimeTests(unittest.TestCase):
    def test_cancellation_token(self):
        token = CancellationToken()
        self.assertFalse(token.cancelled)
        token.cancel()
        self.assertTrue(token.cancelled)
        with self.assertRaises(CancelledError):
            token.raise_if_cancelled()

    def test_estimators_are_monotonic(self):
        self.assertEqual(estimate_stream_copy_bytes(1000, 10, 5), 500)
        self.assertGreater(estimate_precise_bytes(10), estimate_precise_bytes(5))
        self.assertGreater(estimate_gif_bytes(640, 360, 60, 256), estimate_gif_bytes(640, 360, 30, 256))

    def test_frame_snap(self):
        self.assertAlmostEqual(snap_time_to_frame(1.016, 30.0), 1.0, places=6)
        self.assertAlmostEqual(snap_time_to_frame(1.02, 25.0), 1.04, places=6)

    def test_discovery_prefers_configured_and_verifies(self):
        clear_discovery_cache()
        with mock.patch('media_runtime._verify_executable', side_effect=lambda p, **_: p) as verify:
            got = discover_media_tool('ffmpeg', configured='C:/custom/ffmpeg.exe')
        self.assertEqual(got, 'C:/custom/ffmpeg.exe')
        verify.assert_called_once()

    def test_terminate_process_stops_real_child(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        terminate_process(proc, timeout=1.0)
        self.assertIsNotNone(proc.poll())


if __name__ == '__main__':
    unittest.main()
