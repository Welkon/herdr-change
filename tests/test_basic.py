import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import herdr_thread as ht


class BasicTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(ht.slugify("Hello, World!"), "hello-world")
        self.assertEqual(ht.slugify("***"), "thread")

    def test_ci_state(self):
        self.assertEqual(ht.ci_state([]), "none")
        self.assertEqual(ht.ci_state([{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None}]), "pending")
        self.assertEqual(ht.ci_state([{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]), "success")
        self.assertEqual(ht.ci_state([{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}]), "failure")
        self.assertEqual(ht.ci_state([{"status": "QUEUED"}]), "pending")

    def test_ci_failure_wins(self):
        self.assertEqual(ht.ci_state([
            {"status": "IN_PROGRESS", "conclusion": None},
            {"status": "COMPLETED", "conclusion": "FAILURE"},
        ]), "failure")


if __name__ == "__main__":
    unittest.main()
