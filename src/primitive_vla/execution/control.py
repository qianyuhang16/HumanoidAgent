"""Single-controller leases and stale-command fencing."""

from dataclasses import dataclass
from typing import Callable

from primitive_vla.domain.models import SkillCall
from primitive_vla.domain.skills import Candidate


@dataclass(frozen=True)
class Lease:
    call_id: str
    generation: int
    resources: tuple[str, ...]


class ControlAuthority:
    """One controller at a time; a lease is NOT a physical holding state."""

    def __init__(self):
        self.lease: Lease | None = None
        self.generation = 0
        self.revoked = False

    def acquire(self, call: SkillCall, candidate: Candidate) -> Lease:
        if self.lease is not None:
            raise ValueError("Control resources are occupied or awaiting stop acknowledgement")
        self.generation += 1
        self.revoked = False
        self.lease = Lease(call.call_id, self.generation,
                           tuple(r.format(**call.bindings) for r in candidate.spec.resources))
        return self.lease

    def accepts(self, call_id: str, generation: int) -> bool:
        return not self.revoked and self.lease is not None and (call_id, generation) == (self.lease.call_id, self.lease.generation)

    def apply(self, call_id: str, generation: int, command: Callable[[], None]) -> bool:
        # Check at the final dispatch boundary, not only when inference starts.
        # Async/network adapters must repeat this gate inside the robot driver.
        if not self.accepts(call_id, generation):
            return False
        command()
        return True

    def revoke(self) -> None:
        self.revoked = True

    def release(self) -> None:
        self.revoke()
        self.lease = None
