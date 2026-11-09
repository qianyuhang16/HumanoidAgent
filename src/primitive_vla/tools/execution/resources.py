from primitive_vla.contracts import Failure, ToolError


class ResourceManager:
    """Atomic ownership in one event loop, scoped by robot instance.

    Aliases map directly to leaf resources. No built-in whole_body assumptions.
    """

    def __init__(self):
        self._topologies: dict[str, dict[str, frozenset[str]]] = {}
        self._owners: dict[tuple[str, str], str] = {}

    def define_robot(self, robot_id: str, resources: set[str],
                     aliases: dict[str, set[str]] | None = None) -> None:
        aliases = aliases or {}
        if robot_id in self._topologies or resources & aliases.keys() or any(
            not leaves or not leaves <= resources for leaves in aliases.values()
        ):
            raise ValueError("invalid or duplicate robot resource topology")
        self._topologies[robot_id] = {
            **{name: frozenset({name}) for name in resources},
            **{name: frozenset(leaves) for name, leaves in aliases.items()},
        }

    def acquire(self, robot_id: str, resources: tuple[str, ...], call_id: str) -> None:
        if not resources:
            return
        topology = self._topologies.get(robot_id, {})
        unknown = set(resources) - topology.keys()
        if unknown:
            raise ToolError(Failure.INTERFACE, "undeclared robot resources",
                            {"robot_id": robot_id, "resources": sorted(unknown)})
        leaves = set().union(*(topology[resource] for resource in resources))
        conflicts = {leaf: self._owners[(robot_id, leaf)] for leaf in leaves
                     if (robot_id, leaf) in self._owners}
        if conflicts:
            raise ToolError(Failure.RESOURCE, "robot control resources are occupied",
                            {"robot_id": robot_id, "owners": conflicts})
        self._owners.update({(robot_id, leaf): call_id for leaf in leaves})

    def release(self, call_id: str) -> None:
        self._owners = {key: owner for key, owner in self._owners.items() if owner != call_id}

    def occupied(self, robot_id: str) -> dict[str, str]:
        return {leaf: owner for (robot, leaf), owner in self._owners.items() if robot == robot_id}
