"""Real app import contracts, in isolated data directories without accounts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class TauriEntryContractTests(unittest.TestCase):
    def inspect_app(self, *, tauri, port="5000"):
        repo = str(Path(__file__).resolve().parents[1])
        with tempfile.TemporaryDirectory(prefix="chaoxing-p2-entry-") as profile:
            env = dict(os.environ, CHAOXING_DATA_DIR=profile, CHAOXING_PORT=port,
                       PYTHONIOENCODING="utf-8")
            if tauri:
                env["CHAOXING_TAURI"] = "1"
            else:
                env.pop("CHAOXING_TAURI", None)
            script = textwrap.dedent("""
                import json, sys
                sys.path.insert(0, sys.argv[1])
                import app as entry
                if sys.argv[2] == 'tauri':
                    from api.desktop_runtime import register_token_guard
                    register_token_guard(entry.app, 'synthetic-token', 'test-instance', 4321)
                client = entry.app.test_client()
                headers = {'Host':'127.0.0.1:4321', 'X-Auth-Token':'synthetic-token'}
                good = client.get('/api/health', headers=headers)
                origin = client.get('/api/health', headers={**headers, 'Origin':'http://localhost:5000'})
                missing = client.get('/api/health', headers={'Host':'127.0.0.1:4321', 'Origin':'http://localhost:5000'})
                entry.task_store.close()
                print('P2_RESULT=' + json.dumps({
                    'good':good.status_code,
                    'origin_status':origin.status_code,
                    'origin_cors':origin.headers.get('Access-Control-Allow-Origin'),
                    'missing_status':missing.status_code,
                    'missing_cors':missing.headers.get('Access-Control-Allow-Origin'),
                }))
            """)
            result = subprocess.run(
                [sys.executable, "-c", script, repo, "tauri" if tauri else "normal"],
                cwd=profile, env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(next(line.removeprefix("P2_RESULT=")
                                   for line in result.stdout.splitlines()
                                   if line.startswith("P2_RESULT=")))

    def test_tauri_origin_is_rejected_without_cors_even_with_valid_token(self):
        result = self.inspect_app(tauri=True)
        self.assertEqual(result["good"], 200)
        self.assertEqual(result["origin_status"], 401)
        self.assertIsNone(result["origin_cors"])
        self.assertEqual(result["missing_status"], 401)
        self.assertIsNone(result["missing_cors"])

    def test_tauri_ignores_legacy_port_environment(self):
        self.assertEqual(self.inspect_app(tauri=True, port="unused-by-tauri")["good"], 200)

    def test_normal_browser_mode_keeps_cors_and_unauthenticated_health(self):
        result = self.inspect_app(tauri=False)
        self.assertEqual(result["missing_status"], 200)
        self.assertEqual(result["origin_cors"], "http://localhost:5000")


if __name__ == "__main__":
    unittest.main()
