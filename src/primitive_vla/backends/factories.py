"""Constructor adapters for the configurable structured-runtime backends."""


def mock(options, workspace, vla, log, config):
    from .mock import MockBackend
    return MockBackend(config.scene.facts, fault=options.fault, delay_s=options.delay_s,
                       fact_ttl_s=config.state.mock_fact_ttl_s, images=config.scene.images)


def http(options, workspace, vla, log, config):
    from .http import HTTPBackend
    return HTTPBackend(options)


def sapien(options, workspace, vla, log, config):
    from .sapien import SapienBackend
    return SapienBackend(options.simulation, workspace, vla, log)


def libero(options, workspace, vla, log, config):
    from .libero import LiberoBackend
    return LiberoBackend(options.libero, workspace, log)
