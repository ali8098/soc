"""Per-run replay event buffer for the graph plane (issue #72, Phase 0).

The graph runs in the worker process (L2); IR persistence lives behind the
L1 API. Nodes emit beats into an in-memory sink through a contextvar; the
worker drains the sink into ``POST /api/internal/worker/runs/{id}/events``
alongside each heartbeat and once more before completion.

Emission is enrichment, never control flow:

- No sink set (unit tests, ad-hoc graph invocations) → ``emit`` is a no-op.
- Transient flush failure → the batch is requeued for the next flush.
- 409 on flush (lost lease) → the sink goes dead and drops everything;
  the reclaiming worker re-emits, and per-lease idempotency keys dedupe.
"""

from __future__ import annotations

import contextvars
from datetime import UTC, datetime
from typing import Any

from soctalk.core.ir.replay_events import ReplayEvent

EVENT_SINK: contextvars.ContextVar[RunEventSink | None] = contextvars.ContextVar(
    "soctalk_run_event_sink", default=None
)

# Backstop against a pathological emit loop; far above any real run
# (MAX_ITERATIONS bounds supervisor cycles).
MAX_PENDING = 500


class RunEventSink:
    """Ordered buffer of replay events for one run lease."""

    def __init__(self) -> None:
        self._pending: list[dict[str, Any]] = []
        self._ord = 0
        self.dead = False

    def emit(self, event: ReplayEvent) -> None:
        if self.dead or len(self._pending) >= MAX_PENDING:
            return
        self._ord += 1
        self._pending.append(
            {
                "kind": event.kind.value,
                "payload": event.payload,
                "visibility": event.visibility,
                "client_ord": self._ord,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )

    def drain(self) -> list[dict[str, Any]]:
        batch, self._pending = self._pending, []
        return batch

    def requeue(self, batch: list[dict[str, Any]]) -> None:
        """Put a failed batch back at the FRONT so client_ord order holds."""
        if self.dead:
            return
        self._pending[:0] = batch

    def kill(self) -> None:
        self.dead = True
        self._pending = []


def emit(event: ReplayEvent) -> None:
    """Emit from graph code. Safe everywhere: no sink → no-op."""
    sink = EVENT_SINK.get()
    if sink is not None:
        sink.emit(event)


__all__ = ["EVENT_SINK", "MAX_PENDING", "RunEventSink", "emit"]
