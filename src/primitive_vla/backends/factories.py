"""Constructor adapters for the configurable structured-runtime backends."""


def mock(options, workspace, vla, log, config):
    from .mock import MockBackend
    return MockBackend(config.scene.facts, fault=options.fault, delay_s=options.delay_s,
                       fact_ttl_s=config.state.mock_fact_ttl_s, images=config.scene.images)


def sapien(options, workspace, vla, log, config):
    from .sapien import SapienBackend
    policy_factory = None
    if vla is not None and config.adapters.policy:
        from primitive_vla.runtime.composition import load_adapter
        policy_factory = load_adapter(config.adapters.policy)
    return SapienBackend(options.simulation, workspace, vla, log, policy_factory=policy_factory)
