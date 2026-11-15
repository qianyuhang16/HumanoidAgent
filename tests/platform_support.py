import json
from pathlib import Path
import sys

import yaml


def workspace(root):
    platform = root / 'external'
    (platform / 'profiles').mkdir(parents=True)
    (platform / 'skills').mkdir()
    data = {'id': 'external', 'label': 'External platform',
            'environment': {'variables': {'FIXTURE_OPTION': 'connected'}}}
    (platform / 'platform.json').write_text(json.dumps(data))
    for domain in ('state', 'execution', 'verification'):
        path = platform / 'skills/tools' / domain / 'skill.md'
        path.parent.mkdir(parents=True)
        metadata = {'id': f'{domain}.fixture', 'version': '1', 'description': 'Fixture capability', 'tool': {
            'domain': domain, 'provider': 'test-process', 'input_schema': {'type': 'object'},
            'output_schema': {'type': 'object'}, 'required_resources': ['base'] if domain == 'execution' else [],
            'execution_mode': 'async' if domain == 'execution' else 'sync',
            'entrypoint': 'robot_process:bind',
        }}
        path.write_text('---\n'+yaml.safe_dump(metadata,sort_keys=False)+'---\n# Fixture\nObserve when needed.')
    for name in ('coffee', 'desk'):
        skill = f'---\nid: {name}\nversion: "1"\ndescription: {name}\nrequired_tools: [state.fixture]\n---\n# {name}\nObserve when needed.'
        path = platform / 'skills' / name / 'skill.md'
        path.parent.mkdir()
        path.write_text(skill)
        (platform / 'profiles' / f'{name}.json').write_text(json.dumps({
            'id': name, 'label': name, 'environment': {'mode': name},
            'skills': ['skills/tools', f'skills/{name}/skill.md']}))
    return platform


def connection(platform):
    return {'command': sys.executable, 'cwd': str(platform),
            'args': [str(Path(__file__).parent / 'fixtures/robot_process.py')],
            'env': {'PYTHONPATH': str(Path(__file__).parents[1] / 'src')}}
