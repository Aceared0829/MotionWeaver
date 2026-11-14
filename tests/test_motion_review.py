"""固定随机抽样不漏异常，人工记录不改变生产动作状态。"""
import importlib.util
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('review_server',Path(__file__).resolve().parents[1] / "data/tools/review/motion_review_server.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReviewTests(unittest.TestCase):
    def test_sample_is_five_percent_rounded_up_and_reproducible(self):
        entries=[{'id': str(i),'integrity_errors': [],'review_flags': ['visual_quality_not_validated']} for i in range(101)]
        entries.append({'id': 'issue','integrity_errors': [],'review_flags': ['possible_ground_penetration']})
        result,clean=module.choose_cohort(entries,'fixed')
        self.assertEqual(clean,101)
        self.assertEqual(len(result),7)
        self.assertEqual(result[0]['id'],'issue')
        self.assertEqual(result,module.choose_cohort(entries,'fixed')[0])

    def test_integrity_errors_are_not_hidden_by_sampling(self):
        entries=[{'id': 'bad','integrity_errors': ['bad_hash'],'review_flags': []}]
        result,clean=module.choose_cohort(entries,'fixed')
        self.assertEqual(clean,0)
        self.assertEqual(result[0]['group'],'issue')

    def test_review_cap_prioritizes_issues(self):
        entries=[{'id': 'issue'+str(i),'integrity_errors': [],'review_flags': ['possible_ground_penetration']} for i in range(40)]
        entries += [{'id': 'clean'+str(i),'integrity_errors': [],'review_flags': []} for i in range(460)]
        result,clean=module.choose_cohort(entries,'fixed')
        self.assertEqual(clean,460)
        self.assertEqual(len(result),50)
        self.assertEqual(sum(e['group']=='issue' for e in result),40)

    def test_issue_overflow_cannot_silently_pass(self):
        entries=[{'id': str(i),'integrity_errors': ['invalid'],'review_flags': []} for i in range(51)]
        with self.assertRaises(ValueError):module.choose_cohort(entries,'fixed')


if __name__=='__main__':unittest.main()
