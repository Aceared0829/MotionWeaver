import json
from pathlib import Path

import numpy as np

from data.tools.soma_observation import sha, write_json

work = Path('output/soma-speed-profile')
versions = ['baseline-w1', 'baseline-w4', 'baseline-w8', 'optimized-w4', 'optimized-w8', 'baseline-w8-repeat', 'optimized-w8-repeat']
runs = {name: json.loads((work/(name+'.json')).read_text(encoding='utf-8')) for name in versions}
baseline = runs['baseline-w8']['results']
checks = []
for name in ('optimized-w4', 'optimized-w8', 'optimized-w8-repeat'):
    for old, new in zip(baseline, runs[name]['results']):
        assert old['status'] == new['status'] == 'normalized'
        assert old['source_id'] == new['source_id']
        assert old['raw_sha256'] == new['raw_sha256']
        assert old['processed_sha256'] == new['processed_sha256']
        with np.load(old['processing_arrays'], allow_pickle=False) as a, np.load(new['processing_arrays'], allow_pickle=False) as b:
            assert a.files == b.files
            for key in a.files:
                np.testing.assert_array_equal(a[key], b[key])
        before = json.loads(Path(old['observation']).read_text(encoding='utf-8'))
        after = json.loads(Path(new['observation']).read_text(encoding='utf-8'))
        for value in (before, after):
            value['processing'].pop('arrays')
            value['processing'].pop('arrays_sha256')
        assert before == after
        def canonical(record):
            record = dict(record)
            for key in ('raw','processed','observation','observation_sha256','processing_arrays','processing_arrays_sha256'):
                record.pop(key)
            text = json.dumps(record, sort_keys=True)
            return text.replace('20261018-speed-baseline-w8','RUN').replace('20261018-speed-'+name,'RUN')
        assert canonical(old) == canonical(new)
        checks.append({'version':name,'clip_id':new['id'],'frames':new['frames'],
                       'native_files_bitwise_equal':True,'fixed_files_bitwise_equal':True,
                       'processing_arrays_bitwise_equal':True,'observation_and_receipt_semantics_equal':True,
                       'recomposition_error_m':new['recomposition_max_abs_error_m']})
previous_manifest = work/'baseline-code/manifest.json'
previous = json.loads(previous_manifest.read_text(encoding='utf-8'))
receipt = {'previous_manifest_sha256':sha(previous_manifest),
           'code_sha256':{name:sha(Path('data')/name) for name in previous['code_sha256']},
           'equivalent_outputs':True,'checks':checks,
           'timing_runs':[{k:v for k,v in runs[name].items() if k!='results'} for name in versions],
           'timing_limits':'Same 8 source clips and 14926 frames on E:, one host, sequential runs, filesystem cache uncontrolled; not a full-batch ETA.',
           'comparison':'Native and fixed JSON/binary SHA-256 equal; all 9 NPZ arrays exactly equal; observation and receipt paths/archive hashes excluded from semantic comparison.'}
write_json(work/'equivalence.json',receipt)
print(json.dumps({'checks':len(checks),'source_clips':len(baseline),'equivalent_outputs':True,
                  'speedup_round1':runs['baseline-w8']['seconds']/runs['optimized-w8']['seconds'],
                  'speedup_round2':runs['baseline-w8-repeat']['seconds']/runs['optimized-w8-repeat']['seconds']}))
