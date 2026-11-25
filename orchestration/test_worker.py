import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import magpie_worker as worker


class WorkerBoundaryTests(unittest.TestCase):
    def test_development_context_excludes_holdout(self):
        data = json.loads(worker.read_project('data/cases.json'))
        self.assertEqual({row['id'] for row in data['cases']}, {'c1', 'c2', 'c3', 'c4', 'c6'})

    def test_resources_exclude_holdout_only_entries(self):
        data = json.loads(worker.read_project('data/resources.json'))
        rows = [row for group in data.values() if isinstance(group, list) for row in group]
        self.assertFalse({'F7', 'P2', 'CO2'} & {row['id'] for row in rows})
        self.assertTrue(all('c5' not in row['sources'] for row in rows))

    def test_paths_and_secrets_stay_outside_worker(self):
        for name in ['../escape', '.git/config', 'backend/.env']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                worker.project_path(name)

    def test_writes_restricted_to_owner(self):
        with self.assertRaises(ValueError):
            worker.execute('write_file', {'path': 'data/forbidden.py', 'content': ''}, ['backend/'], [])
        with self.assertRaises(ValueError):
            worker.execute('write_file', {'path': 'backend/app/media/test.py', 'content': ''}, ['backend/'], ['backend/app/media/'])

    def test_write_file_is_literal_text(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'ROOT', Path(directory).resolve()):
            worker.execute('write_file', {'path': 'frontend/src/test.ts', 'content': 'literal $() text'}, ['frontend/'], [])
            self.assertEqual((Path(directory) / 'frontend/src/test.ts').read_text(), 'literal $() text')

    def test_symlink_cannot_escape(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            (Path(directory) / 'frontend').symlink_to(outside)
            with patch.object(worker, 'ROOT', Path(directory).resolve()), self.assertRaises(ValueError):
                worker.project_path('frontend/escaped.ts')

    def test_copy_respects_write_scope(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'ROOT', Path(directory).resolve()):
            root = Path(directory)
            (root / 'source.json').write_text('{"literal": true}')
            worker.execute('copy_file', {'source': 'source.json', 'path': 'frontend/data.json'}, ['frontend/'], [])
            self.assertEqual((root / 'frontend/data.json').read_text(), '{"literal": true}')
            with self.assertRaises(ValueError):
                worker.execute('copy_file', {'source': 'source.json', 'path': 'data/forbidden.json'}, ['frontend/'], [])

    def test_replace_is_exact_and_nonmatching_edits_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'ROOT', Path(directory).resolve()):
            p = Path(directory) / 'frontend/test.py'
            p.parent.mkdir()
            p.write_text('old old')
            with self.assertRaises(ValueError):
                worker.execute('replace_text', {'path': 'frontend/test.py', 'old_text': 'old', 'new_text': 'new', 'expected_occurrences': 1}, ['frontend/'], [])
            self.assertEqual(p.read_text(), 'old old')
            worker.execute('replace_text', {'path': 'frontend/test.py', 'old_text': 'old', 'new_text': 'new', 'expected_occurrences': 2}, ['frontend/'], [])
            self.assertEqual(p.read_text(), 'new new')


if __name__ == '__main__':
    unittest.main()
