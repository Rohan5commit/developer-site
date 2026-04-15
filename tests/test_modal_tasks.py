import base64
import tempfile
import unittest
from pathlib import Path

import modal_tasks


class ModalTasksTests(unittest.TestCase):
    def test_ignore_local_path_filters_generated_artifacts(self) -> None:
        self.assertTrue(
            modal_tasks._ignore_local_path(
                Path('nestjs-fastify-boilerplate/node_modules/typescript/lib/typescript.js'),
            ),
        )
        self.assertTrue(modal_tasks._ignore_local_path(Path('.gemini/.env')))
        self.assertFalse(modal_tasks._ignore_local_path(Path('scripts/modal_exec.sh')))

    def test_validate_rel_path_rejects_unsafe_segments(self) -> None:
        with self.assertRaises(ValueError):
            modal_tasks._validate_rel_path('../secret')

        modal_tasks._validate_rel_path('scripts/modal_exec.sh')

    def test_apply_repo_changes_writes_and_removes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            stale = root / 'stale.txt'
            stale.write_text('old', encoding='utf-8')

            changes = {
                'removed': ['stale.txt'],
                'updated': [
                    {
                        'path': 'fresh.txt',
                        'kind': 'file',
                        'mode': 0o644,
                        'content_b64': base64.b64encode(b'new').decode('ascii'),
                    }
                ],
            }

            modal_tasks._apply_repo_changes(str(root), changes)

            self.assertFalse(stale.exists())
            self.assertEqual((root / 'fresh.txt').read_text(encoding='utf-8'), 'new')


if __name__ == '__main__':
    unittest.main()
