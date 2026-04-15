import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import primary_compute


class PrimaryComputeTests(unittest.TestCase):
    def test_run_heavy_local_mode_returns_local_execution(self) -> None:
        result = primary_compute.run_heavy(
            {"iterations": 1_000, "workers": 1, "salt": 17},
            force_mode="local",
        )
        self.assertEqual(result["execution"], "local")
        self.assertEqual(result["iterations"], 1_000)
        self.assertEqual(result["workers"], 1)

    def test_read_state_resets_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / 'usage.json'
            state_path.write_text('{not-json', encoding='utf-8')

            with mock.patch.object(primary_compute, 'STATE_PATH', state_path):
                state = primary_compute._read_state()

        self.assertEqual(state['used_min'], 0.0)
        self.assertIn('day', state)

    def test_write_state_persists_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / 'usage.json'
            payload = {'day': '2026-04-15', 'used_min': 12.5}

            with mock.patch.object(primary_compute, 'STATE_PATH', state_path):
                primary_compute._write_state(payload)

            written = json.loads(state_path.read_text(encoding='utf-8'))

        self.assertEqual(written, payload)


if __name__ == '__main__':
    unittest.main()
