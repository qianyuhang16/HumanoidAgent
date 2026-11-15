"""Discover platforms and run profiles from workspace files, without importing them."""
from dataclasses import dataclass
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from primitive_vla.skills import SkillStore

__all__ = ['AssemblyCatalog', 'Platform', 'RunProfile']

PLATFORM_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['id', 'label', 'connection'],
    'properties': {'id': {'type': 'string', 'minLength': 1}, 'label': {'type': 'string', 'minLength': 1},
        'options': {'type': 'object'}, 'connection': {
            'type': 'object', 'additionalProperties': False, 'required': ['transport', 'command'],
            'properties': {'transport': {'const': 'stdio'}, 'command': {'type': 'string', 'minLength': 1},
                           'args': {'type': 'array', 'items': {'type': 'string'}},
                           'cwd': {'type': 'string'},
                           'env': {'type': 'object', 'additionalProperties': {'type': 'string'}}}}}}
PROFILE_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['id', 'label', 'skills'],
    'properties': {'id': {'type': 'string', 'minLength': 1}, 'label': {'type': 'string', 'minLength': 1},
                   'environment': {'type': 'object'}, 'skills': {'type': 'array', 'uniqueItems': True,
                     'items': {'type': 'string', 'minLength': 1}}}}


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    root: Path
    connection: dict
    options: dict


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


class AssemblyCatalog:
    def __init__(self, root: str | Path):
        self._platforms, self._profiles = {}, {}
        for path in sorted(Path(root).resolve().glob('*/platform.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            Draft202012Validator(PLATFORM_SCHEMA).validate(data)
            platform = Platform(data['id'], data['label'], path.parent, data['connection'], data.get('options', {}))
            if platform.id in self._platforms:
                raise ValueError(f'duplicate platform: {platform.id}')
            self._platforms[platform.id] = platform
            for file in sorted((path.parent / 'profiles').glob('*.json')):
                item = json.loads(file.read_text(encoding='utf-8'))
                Draft202012Validator(PROFILE_SCHEMA).validate(item)
                key = (platform.id, item['id'])
                if key in self._profiles:
                    raise ValueError(f'duplicate profile: {key}')
                self._profiles[key] = RunProfile(item['id'], item['label'], item.get('environment', {}),
                                                tuple((path.parent / name).resolve() for name in item['skills']))

    def catalog(self):
        return [{'id': platform.id, 'label': platform.label, 'profiles': [
            {'id': profile.id, 'label': profile.label}
            for (owner, _), profile in self._profiles.items() if owner == platform.id
        ]} for platform in self._platforms.values()]

    def select(self, platform_id, profile_id):
        return self._platforms[platform_id], self._profiles[(platform_id, profile_id)]
