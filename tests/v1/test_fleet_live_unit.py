"""Unit tests for fleet-live stage mapping (issue #72). DB-free."""

from __future__ import annotations

from soctalk.core.api.fleet_day import stage_for_latest_event


def test_stage_mapping_covers_all_replay_kinds():
    assert stage_for_latest_event("alert_ingested", {}) == "gate"
    assert stage_for_latest_event("policy_resolved", {}) == "gate"
    assert stage_for_latest_event("supervisor_decision", {}) == "sup"
    assert stage_for_latest_event("worker_started", {"worker": "wazuh"}) == "wazuh"
    assert stage_for_latest_event("worker_started", {"worker": "cortex"}) == "cortex"
    assert stage_for_latest_event("worker_started", {"worker": "misp"}) == "misp"
    assert (
        stage_for_latest_event("worker_started", {"worker": "authorization_context"})
        == "authz"
    )
    assert stage_for_latest_event("worker_result", {"worker": "wazuh"}) == "sup"
    assert stage_for_latest_event("verdict_rendered", {}) == "verdict"
    assert stage_for_latest_event("guard_evaluated", {"effect": "pass"}) == "guard"
    assert stage_for_latest_event("human_review_requested", {}) == "human"
    assert stage_for_latest_event("human_decision", {}) == "human"
    assert stage_for_latest_event("auto_closed", {}) == "close"


def test_stage_mapping_is_honest_about_unknowns():
    assert stage_for_latest_event("hypothesis_updated", {}) == "unknown"
    assert stage_for_latest_event("nonsense_kind", None) == "unknown"
    assert stage_for_latest_event("worker_started", {"worker": "not-a-worker"}) == "sup"
