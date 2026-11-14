"""Keep daily CLI checks independent of optional historical metadata audits."""
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/verify_repository_layout.py'
spec = importlib.util.spec_from_file_location('verify_repository_layout', SCRIPT)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class RepositoryLayoutAuditTests(unittest.TestCase):
    def test_audit_modes(self):
        for mode, expected in [('daily', None), ('matching', None),
                               ('changed', SystemExit), ('missing_receipt', FileNotFoundError)]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                script = root / 'data/tools/sample.py'
                script.parent.mkdir(parents=True)
                script.write_text('import argparse\nargparse.ArgumentParser().parse_args()', encoding='utf-8')
                nested = root / 'data/tools/build/nested.py'
                nested.parent.mkdir(parents=True)
                nested.write_text('import argparse\nargparse.ArgumentParser().parse_args()', encoding='utf-8')
                artifact = root / 'training/runs/sample.ckpt'
                artifact.parent.mkdir(parents=True)
                artifact.write_bytes(b'fixture')
                audit = root / '.build/repository-migration-20260912'
                audit.mkdir(parents=True)
                if mode != 'missing_receipt':
                    (audit / 'moves.json').write_text(json.dumps({'old': 'training/runs'}))
                    metadata = [artifact.stat().st_size, artifact.stat().st_mtime_ns]
                    if mode in {'daily', 'changed'}:
                        metadata[0] += 1
                    (audit / 'artifact-inventory.json').write_text(json.dumps({'old': {'sample.ckpt': metadata}}))
                output = root / 'report.json'
                argv = [str(SCRIPT), '--output', str(output)]
                if mode != 'daily':
                    argv += ['--migration-audit', str(audit)]
                cli = subprocess.CompletedProcess([], 0, 'usage: sample', '')
                with patch.object(verifier, 'ROOT', root), patch.object(sys, 'argv', argv), \
                     patch.object(verifier.subprocess, 'run', return_value=cli), \
                     contextlib.redirect_stdout(io.StringIO()):
                    if expected:
                        with self.assertRaises(expected) as raised:
                            verifier.main()
                        if expected is SystemExit:
                            self.assertEqual(raised.exception.code, 1)
                    else:
                        verifier.main()
                if mode != 'missing_receipt':
                    report = json.loads(output.read_text(encoding='utf-8'))
                    self.assertEqual(report['cli_count'], 2)
                    if mode == 'daily':
                        self.assertNotIn('artifact_files', report)
                    else:
                        self.assertEqual(report['artifact_files'], 1)
                        self.assertEqual(bool(report['artifact_metadata_changed']), mode == 'changed')


if __name__ == '__main__':
    unittest.main()
