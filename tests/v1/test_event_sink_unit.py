"""Unit tests for the worker replay event sink (issue #72). DB-free."""

from __future__ import annotations

import asyncio

from soctalk.core.ir import replay_events
from soctalk.graph.event_sink import EVENT_SINK, MAX_PENDING, RunEventSink, emit


def _ev(n: int):
    return replay_events.worker_started(f"w{n}")


def test_emit_without_sink_is_noop():
    assert EVENT_SINK.get() is None
    emit(_ev(1))  # must not raise


def test_client_ord_monotonic_and_drain_clears():
    sink = RunEventSink()
    for i in range(3):
        sink.emit(_ev(i))
    batch = sink.drain()
    assert [b["client_ord"] for b in batch] == [1, 2, 3]
    assert sink.drain() == []


def test_requeue_preserves_order_across_new_emissions():
    sink = RunEventSink()
    sink.emit(_ev(1))
    sink.emit(_ev(2))
    batch = sink.drain()
    sink.emit(_ev(3))
    sink.requeue(batch)
    ords = [b["client_ord"] for b in sink.drain()]
    assert ords == [1, 2, 3]


def test_kill_drops_and_stops():
    sink = RunEventSink()
    sink.emit(_ev(1))
    sink.kill()
    assert sink.drain() == []
    sink.emit(_ev(2))
    assert sink.drain() == []
    sink.requeue([{"client_ord": 1}])
    assert sink.drain() == []


def test_pending_backstop():
    sink = RunEventSink()
    for i in range(MAX_PENDING + 50):
        sink.emit(_ev(i))
    assert len(sink.drain()) == MAX_PENDING


def test_contextvar_isolated_per_task():
    async def run_task(name: str, results: dict):
        sink = RunEventSink()
        token = EVENT_SINK.set(sink)
        try:
            await asyncio.sleep(0)
            emit(replay_events.worker_started(name))
            results[name] = [b["payload"]["worker"] for b in sink.drain()]
        finally:
            EVENT_SINK.reset(token)

    async def main():
        results: dict = {}
        await asyncio.gather(run_task("a", results), run_task("b", results))
        return results

    results = asyncio.run(main())
    assert results == {"a": ["a"], "b": ["b"]}
