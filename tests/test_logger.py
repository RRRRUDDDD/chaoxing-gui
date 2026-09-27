"""Console logging must survive a missing stderr and stay plain on pipes."""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# stderr is None while api.logger is imported, exactly like the console=False
# exe, and is redirected afterwards (app.py swaps in devnull the same way).
SCRIPT = r"""
import io, sys
real = sys.stderr
sys.stderr = None
from api.logger import logger
captured = io.StringIO()
sys.stderr = captured
for index in range(3):
    logger.info("line {}", index)
logger.debug("hidden debug")
logger.complete()
sys.stderr = real
sys.stdout.write(captured.getvalue())
"""


class ConsoleLoggerTests(unittest.TestCase):
    def test_late_stderr_receives_plain_lines_without_sink_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "PYTHONPATH": ROOT, "PYTHONUTF8": "1"}
            result = subprocess.run(
                [sys.executable, "-c", SCRIPT], cwd=directory, env=env,
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout
        self.assertNotIn("Logging error in Loguru Handler", output + result.stderr)
        for index in range(3):
            self.assertIn(f"line {index}", output)
        self.assertNotIn("\x1b[", output)
        self.assertNotIn("hidden debug", output)


if __name__ == "__main__":
    unittest.main()
