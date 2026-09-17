"""Summarize the entire declared batch, including failures and missing episodes."""

import argparse
import json
from math import sqrt
from pathlib import Path


def summarize(directory):
    manifest = json.loads((directory/'manifest.json').read_text())
    outcomes, pending = [], []
    for seed in manifest['seeds']:
        for mode in manifest['modes']:
            path = directory/f'{mode}-{seed}'/'summary.json'
            if not path.exists():
                pending.append(f'{mode}-{seed}')
                continue
            report = json.loads(path.read_text())
            success = report['physical_success'] and report['final_observation_available']
            outcomes.append({'seed': seed, 'mode': mode, 'success': bool(success),
                             'reason': report.get('reason', report.get('error')),
                             'vla_steps': report['physical'].get('control_steps'),
                             'agent_calls': report['agent_calls'],
                             'handoff_calls': report.get('handoff_calls', 0), 'evidence': str(path)})
    n = len(manifest['seeds']) * len(manifest['modes'])
    successes = sum(row['success'] for row in outcomes)
    result = {'expected': n, 'completed': len(outcomes), 'pending': pending,
              'successes': successes, 'revision': manifest['revision'], 'tier': manifest['tier'],
              'acceptance_met': (manifest['tier'] == 'test' and n >= 20 and not pending
                                 and len(manifest['modes']) == 1 and successes/n >= .5),
              'episodes': outcomes}
    if not pending:
        rate, z = successes/n, 1.959963984540054
        center = (rate + z*z/(2*n))/(1+z*z/n)
        radius = z*sqrt(rate*(1-rate)/n + z*z/(4*n*n))/(1+z*z/n)
        result.update(success_rate=rate, wilson_95=[center-radius, center+radius])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    result = summarize(args.directory)
    (args.directory/'metrics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'episodes'}, ensure_ascii=False))
