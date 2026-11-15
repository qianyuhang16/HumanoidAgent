"""Load a self-contained tool package inside its platform process."""
from dataclasses import dataclass
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from primitive_vla.skills import SkillStore

PLATFORM_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['id', 'label'],
    'properties': {
        'id': {'type': 'string', 'minLength': 1}, 'label': {'type': 'string', 'minLength': 1},
        'options': {'type': 'object'},
        'environment': {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'python_paths': {'type': 'array', 'items': {'type': 'string'}},
                'variables': {'type': 'object', 'additionalProperties': {'type': 'string'}},
            },
        },
    },
}
PROFILE_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['id', 'label', 'skills'],
    'properties': {'id': {'type': 'string', 'minLength': 1}, 'label': {'type': 'string', 'minLength': 1},
                   'environment': {'type': 'object'}, 'skills': {'type': 'array', 'uniqueItems': True,
                     'items': {'type': 'string', 'minLength': 1}}}}


@dataclass(frozen=True)
class RunProfile:
    id: str
    label: str
    environment: dict
    skills: tuple[Path, ...]

    def load_skills(self) -> SkillStore:
        skills = SkillStore()
        for path in self.skills:
            skills.load(path)
        names = {definition['spec']['name'] for definition in skills.tool_definitions()}
        missing = {name for skill in skills.catalog() for name in skill['required_tools'] if name not in names}
        if missing:
            raise ValueError(f'Skills require unavailable tools: {sorted(missing)}')
        return skills


class PlatformPackage:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        data = json.loads((self.root / 'platform.json').read_text(encoding='utf-8'))
        Draft202012Validator(PLATFORM_SCHEMA).validate(data)
        self.id, self.label = data['id'], data['label']
        self.environment, self.options = data.get('environment', {}), data.get('options', {})
        self.profiles = {}
        for file in sorted((self.root / 'profiles').glob('*.json')):
            item = json.loads(file.read_text(encoding='utf-8'))
            Draft202012Validator(PROFILE_SCHEMA).validate(item)
            if item['id'] in self.profiles:
                raise ValueError(f'duplicate profile: {item["id"]}')
            self.profiles[item['id']] = RunProfile(item['id'], item['label'], item.get('environment', {}),
                                                   tuple((self.root / name).resolve() for name in item['skills']))

    def catalog(self):
        return {'id': self.id, 'label': self.label, 'profiles': [
            {'id': profile.id, 'label': profile.label} for profile in self.profiles.values()]}

    def assemble(self, profile_id, options):
        profile = self.profiles[profile_id]
        if options.keys() & profile.environment.keys():
            raise ValueError('assembly options cannot override the selected profile environment')
        return profile, profile.load_skills(), {**self.options, **options, **profile.environment}
