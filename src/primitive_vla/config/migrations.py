"""Explicit compatibility for retired configuration fields, shared by all inputs."""

import warnings


RETIRED_FIELDS = {
    'scene': {'mode': 'Observation sources are selected by observer_backend.'},
    'libero': {'objects': 'This mapping was unused; scene.objects and predicates define the bindings.'},
    'libero.policy': {'model_repo': 'The loaded model is selected by checkpoint.'},
}


def migrate_section(section, data):
    if not isinstance(data, dict):
        return data
    migrated = dict(data)
    for field, explanation in RETIRED_FIELDS[section].items():
        if field in migrated:
            migrated.pop(field)
            warnings.warn(f'{section}.{field} is retired and was ignored. {explanation}',
                          FutureWarning, stacklevel=3)
    return migrated
