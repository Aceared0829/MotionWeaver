import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from data.tools.review.motion_review_server import ReviewStore
from data.tools.ops.run_overnight_seed_pipeline import isolate_issues, run


class SecondaryReviewTests(unittest.TestCase):
    def test_bad_motion_preserves_files_and_exits_cleanup_population(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'reports').mkdir()
            (root/'reviews').mkdir()
            source=root/'bad.bvh'
            output=root/'bad.npz'
            source.write_bytes(b'original')
            output.write_bytes(b'output')
            entries=[{'id': 'bad','integrity_errors': [],'review_flags': ['possible_ground_penetration']},
                     {'id': 'hash','integrity_errors': ['output_hash_mismatch'],'review_flags': []},
                     {'id': 'good','integrity_errors': [],'review_flags': ['visual_quality_not_validated']}]
            (root/'reports/pending_quality_screen.json').write_text(json.dumps({'reports': entries}))
            with closing(sqlite3.connect(root/'catalog.sqlite3')) as db, db:
                db.execute('CREATE TABLE motions(id TEXT,state TEXT,error TEXT)')
                db.execute('CREATE TABLE events(motion_id TEXT,state TEXT,detail TEXT)')
                db.executemany('INSERT INTO motions VALUES(?,?,NULL)',[(e['id'],'retargeted_pending_quality') for e in entries])
            self.assertEqual(isolate_issues(root),2)
            isolate_issues(root)
            with closing(sqlite3.connect(root/'catalog.sqlite3')) as db, db:
                self.assertEqual(db.execute("SELECT id FROM motions WHERE state='retargeted_pending_quality'").fetchall(),[('good',)])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],2)
            self.assertEqual(source.read_bytes(),b'original')
            self.assertEqual(output.read_bytes(),b'output')

    def test_appending_exceptions_preserves_decisions_and_pages_all_items(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'reviews').mkdir()
            entries=[{'id': str(i),'source_sha256': 'bound','integrity_errors': []} for i in range(51)]
            first=ReviewStore(root,root,entries[:1],'first',1)
            first.save({'id': '0','status': 'approved','note': 'watched'})
            expanded=ReviewStore(root,root,entries,'expanded',1)
            self.assertEqual(expanded.decisions()['0']['status'],'approved')
            self.assertEqual(len(expanded.ids),50)
            second=ReviewStore(root,root,entries,'expanded',2)
            self.assertEqual(second.ids,{'50'})
            self.assertTrue((root/'reviews/decisions.sqlite3').exists())

    def test_user_stop_prevents_publication_and_queue_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'work').mkdir()
            stop = root / 'work/STOP'
            stop.write_text('user_pause: keep all files', encoding='utf-8')
            with patch('data.tools.ops.run_overnight_seed_pipeline.states') as state, \
                 patch('data.tools.ops.run_overnight_seed_pipeline.finish_batch') as finish, \
                 patch('data.tools.ops.run_overnight_seed_pipeline.start_queue') as start:
                run(root, root, once=True)
            state.assert_not_called()
            finish.assert_not_called()
            start.assert_not_called()
            self.assertEqual(stop.read_text(encoding='utf-8'), 'user_pause: keep all files')
