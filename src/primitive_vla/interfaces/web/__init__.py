"""Optional local browser workbench. Install primitive-vla[gui] to use it."""


def create_app(*args, **kwargs):
    from .app import create_app as factory

    return factory(*args, **kwargs)
