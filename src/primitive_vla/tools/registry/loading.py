"""Bind file-declared tools to implementations in a platform's own Python environment."""
from importlib import import_module

from primitive_vla.contracts import Domain
from primitive_vla.tools.contracts import ToolSpec


def register_tools(registry, definitions, context):
    for definition in definitions:
        data = definition['spec']
        spec = ToolSpec(**{**data, 'domain': Domain(data['domain']),
                          'supported_robots': tuple(data.get('supported_robots', ())),
                          'required_resources': tuple(data.get('required_resources', ()))})
        module, name = definition['entrypoint'].split(':', 1)
        factory = getattr(import_module(module), name)
        registry.register(spec, factory(context, spec, definition['options']))
