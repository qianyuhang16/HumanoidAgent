from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator
import yaml

METADATA = Draft202012Validator({
    'type': 'object', 'additionalProperties': False,
    'required': ['id', 'version', 'description'],
    'properties': {
        'id': {'type': 'string', 'minLength': 1},
        'version': {'type': 'string', 'minLength': 1},
        'description': {'type': 'string', 'minLength': 1},
        'required_tools': {'type': 'array', 'uniqueItems': True,
                           'items': {'type': 'string', 'minLength': 1}},
        'tool': {
            'type': 'object', 'additionalProperties': False,
            'required': ['domain', 'input_schema', 'output_schema', 'provider', 'entrypoint'],
            'properties': {
                'domain': {'enum': ['state', 'execution', 'verification']},
                'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'},
                'provider': {'type': 'string', 'minLength': 1},
                'supported_robots': {'type': 'array', 'items': {'type': 'string'}},
                'required_resources': {'type': 'array', 'items': {'type': 'string'}},
                'execution_mode': {'enum': ['sync', 'async']},
                'timeout': {'type': 'number', 'exclusiveMinimum': 0},
                'entrypoint': {'type': 'string', 'pattern': r'^[\w.]+:[A-Za-z_]\w*$'},
                'options': {'type': 'object'},
            },
        },
    },
})


@dataclass(frozen=True)
class Skill:
    id: str
    version: str
    description: str
    required_tools: tuple[str, ...]
    content: str
    body: str
    tool: dict | None = None


class SkillStore:
    """Discover skill.md files. Tool contracts live in metadata; rules remain prose."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def load(self, source: str | Path) -> None:
        source = Path(source).resolve()
        paths = sorted(source.rglob('skill.md')) if source.is_dir() else [source]
        for path in paths:
            if path.suffix != '.md':
                raise ValueError(f'Skill requires a Markdown file: {path}')
            self.add(path.read_text(encoding='utf-8'))

    def add(self, text: str) -> None:
        if not text.startswith('---\n'):
            raise ValueError('Skill requires Markdown with YAML metadata')
        metadata, body = text[4:].split('\n---\n', 1)
        item = yaml.safe_load(metadata)
        METADATA.validate(item)
        if item['id'] in self._skills:
            raise ValueError(f'duplicate Skill: {item["id"]}')
        self._skills[item['id']] = Skill(
            item['id'], item['version'], item['description'],
            tuple(item.get('required_tools', ())), text, body, item.get('tool'),
        )

    def documents(self) -> list[str]:
        return [skill.content for skill in self._skills.values()]

    def tool_definitions(self) -> list[dict]:
        """The same selected files supply both callable contracts and readable knowledge."""
        definitions = []
        for skill in self._skills.values():
            if skill.tool is not None:
                spec = {key: value for key, value in skill.tool.items()
                        if key not in {'entrypoint', 'options'}}
                definitions.append({
                    'spec': {'name': skill.id, 'description': skill.description, **spec},
                    'entrypoint': skill.tool['entrypoint'], 'options': skill.tool.get('options', {}),
                })
        return definitions

    def catalog(self) -> list[dict]:
        return [{'id': skill.id, 'version': skill.version, 'description': skill.description,
                 'required_tools': list(skill.required_tools)} for skill in self._skills.values()]

    def read(self, skill_id: str, offset: int = 0, limit: int = 8000) -> dict:
        if offset < 0 or not 1 <= limit <= 16000:
            raise ValueError('invalid skill page bounds')
        skill = self._skills[skill_id]
        # Deployable Markdown stays in documents(); readers need usage and parameters.
        body = skill.body
        result = {'id': skill.id, 'version': skill.version, 'content': body[offset:offset + limit],
                  'offset': offset, 'total_chars': len(body)}
        if skill.tool is not None:
            result['input_schema'] = skill.tool['input_schema']
        return result
