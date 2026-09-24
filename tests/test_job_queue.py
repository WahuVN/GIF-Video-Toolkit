import threading
import time
import unittest

from media_job_queue import SerialJobQueue


class JobQueueTests(unittest.TestCase):
    def test_serial_order_and_states(self):
        events = []
        done = threading.Event()
        q = SerialJobQueue(lambda s: (events.append((s.label, s.status)), done.set() if s.label == 'b' and s.status == 'done' else None))
        order = []
        q.submit('a', lambda token, progress: (order.append('a'), progress(0.5, 'half')))
        q.submit('b', lambda token, progress: order.append('b'))
        self.assertTrue(done.wait(3))
        self.assertEqual(order, ['a', 'b'])
        self.assertIn(('a', 'done'), events)
        self.assertIn(('b', 'done'), events)
        q.close()

    def test_cancel_queued(self):
        gate = threading.Event()
        q = SerialJobQueue()
        first = q.submit('first', lambda token, progress: gate.wait(1))
        second = q.submit('second', lambda token, progress: None)
        time.sleep(0.05)
        self.assertTrue(q.cancel(second))
        gate.set()
        time.sleep(0.15)
        states = {s.id: s.status for s in q.snapshots()}
        self.assertEqual(states[second], 'cancelled')
        q.close()


if __name__ == '__main__':
    unittest.main()
