"""Runtime service protocol and restart policy primitives.

A :class:`Service` is a long-running unit of work driven by the
:class:`~assemblage.runtime.supervisor.Supervisor`. Each service runs on its
own named thread; the supervisor passes a ``stop`` event the service must
observe to exit promptly, and calls :meth:`Service.request_stop` to nudge a
blocking service awake (e.g. a consumer parked in ``start_consuming``).
"""

import random
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class Service(ABC):
    """A supervised, long-running unit of work.

    Implementations run on a dedicated thread. ``run`` should return only when
    ``stop`` is set (graceful shutdown) or raise (the supervisor restarts it
    per its :class:`RestartPolicy`).
    """

    name: str

    @abstractmethod
    def run(self, stop: threading.Event) -> None:
        """Do the work, observing ``stop`` and returning promptly when set."""

    def request_stop(self) -> None:
        """Nudge a blocking ``run`` awake. Called from the supervisor thread.

        Default is a no-op; services that block in a foreign event loop
        (pika ``start_consuming``) override this to interrupt it thread-safely.
        """
        return None


@dataclass(frozen=True)
class Backoff:
    """Exponential backoff with proportional jitter.

    ``delay(attempt)`` returns ``initial * factor**attempt`` capped at
    ``maximum``, perturbed by up to +/- ``jitter`` (a fraction of the base).
    ``attempt`` is 0-based: the first retry passes ``attempt=0``.
    """

    initial: float = 1.0
    maximum: float = 60.0
    factor: float = 2.0
    jitter: float = 0.25

    def delay(self, attempt: int) -> float:
        base = min(self.maximum, self.initial * self.factor**attempt)
        if self.jitter:
            spread = base * self.jitter
            base += random.uniform(-spread, spread)
        return max(0.0, base)


@dataclass(frozen=True)
class RestartPolicy:
    """How the supervisor reacts when a service's ``run`` returns or raises.

    ``restart`` — restart the service (with ``backoff``) instead of leaving it
    stopped. ``reset_after`` — if a service stays up this many seconds, its
    backoff attempt counter resets so a later crash retries quickly again.
    """

    restart: bool = True
    backoff: Backoff = field(default_factory=Backoff)
    reset_after: float = 300.0
