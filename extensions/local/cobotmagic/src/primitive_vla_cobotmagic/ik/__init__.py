"""Pixel-to-motion workflow; numerical implementation lives within this package."""


def main(argv=None):
    from .cli import main as run
    return run(argv)


def move_to_pixel(arm, u, v, *, config=None, execute=False):
    """Plan a front-image pixel; execute=True waits for terminal Space before motion."""
    from .pixel import DEFAULT_CONFIG, move_to_pixel as run
    return run(arm, u, v, config=DEFAULT_CONFIG if config is None else config, execute=execute)
