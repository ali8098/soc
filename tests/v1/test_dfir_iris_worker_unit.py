"""Unit tests for DFIR-IRIS worker node — DB-free, MCP client mocked."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soctalk.workers.dfir_iris import (
    dfir_iris_worker_node,
    _extract_case_id,
    _find_related_cases,
    _search_iocs_from_alert,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(tool_results: dict) -> AsyncMock:
    """Build a mock MCP client whose call_tool returns preset results."""
    client = AsyncMock()
    async def call_tool(name, params=None):
        return tool_results.get(name, "[]")
    client.call_tool = call_tool
    return client


def _base_state(**kwargs) -> dict:
    state = {
        "investigation": {
            "alerts": [],
            "findings": [],
            "metadata": {},
        },
        "supervisor_decision": {"specific_instructions": ""},
    }
    state.update(kwargs)
    return state


# ---------------------------------------------------------------------------
# _extract_case_id
# ---------------------------------------------------------------------------

def test_extract_case_id_returns_first_hit():
    hits = [{"ioc": "1.2.3.4", "cases": json.dumps([{"case_id": 42}])}]
    assert _extract_case_id(hits) == 42


def test_extract_case_id_empty():
    assert _extract_case_id([]) is None


def test_extract_case_id_bad_json():
    hits = [{"ioc": "x", "cases": "not-json"}]
    assert _extract_case_id(hits) is None


# ---------------------------------------------------------------------------
# _find_related_cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_related_cases_stores_result():
    client = _make_client({"list_cases": json.dumps([{"case_id": 1, "case_name": "Test"}])})
    state = _base_state()
    result = await _find_related_cases(client, state)
    assert "dfir_iris_open_cases" in result["investigation"]["metadata"]


# ---------------------------------------------------------------------------
# _search_iocs_from_alert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_iocs_no_observables_returns_unchanged():
    client = _make_client({})
    state = _base_state()
    result = await _search_iocs_from_alert(client, state)
    assert result["investigation"]["metadata"].get("dfir_iris_ioc_pivot") is None


@pytest.mark.asyncio
async def test_search_iocs_hit_creates_finding():
    cases_json = json.dumps([{"case_id": 7, "case_name": "APT29"}])
    client = _make_client({"search_iocs": cases_json})
    state = _base_state()
    state["investigation"]["alerts"] = [
        {"observables": [{"value": "evil.com"}]}
    ]
    result = await _search_iocs_from_alert(client, state)
    findings = result["investigation"]["findings"]
    assert len(findings) == 1
    assert "IOC pivot" in findings[0]["description"]
    pivot = result["investigation"]["metadata"]["dfir_iris_ioc_pivot"]
    assert pivot[0]["ioc"] == "evil.com"


@pytest.mark.asyncio
async def test_search_iocs_empty_result_no_finding():
    client = _make_client({"search_iocs": "[]"})
    state = _base_state()
    state["investigation"]["alerts"] = [
        {"observables": [{"value": "clean.com"}]}
    ]
    result = await _search_iocs_from_alert(client, state)
    assert result["investigation"]["findings"] == []


# ---------------------------------------------------------------------------
# dfir_iris_worker_node — top-level routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_node_default_searches_iocs():
    cases_json = json.dumps([{"case_id": 1}])
    client = _make_client({"search_iocs": cases_json, "list_cases": "[]"})
    state = _base_state()
    state["investigation"]["alerts"] = [
        {"observables": [{"value": "1.1.1.1"}]}
    ]
    with patch("soctalk.workers.dfir_iris.get_dfir_iris_client", return_value=client), \
         patch("soctalk.workers.dfir_iris.emit_replay"):
        result = await dfir_iris_worker_node(state)
    assert result.get("last_error") is None


@pytest.mark.asyncio
async def test_worker_node_client_error_sets_last_error():
    client = AsyncMock()
    client.call_tool.side_effect = RuntimeError("connection refused")
    state = _base_state()
    with patch("soctalk.workers.dfir_iris.get_dfir_iris_client", return_value=client), \
         patch("soctalk.workers.dfir_iris.emit_replay"):
        result = await dfir_iris_worker_node(state)
    assert result["last_error"] is not None
    assert "DFIR-IRIS worker error" in result["last_error"]


@pytest.mark.asyncio
async def test_worker_node_pivot_instruction_routes_correctly():
    client = _make_client({"search_iocs": "[]"})
    state = _base_state()
    state["supervisor_decision"] = {"specific_instructions": "pivot on IOCs"}
    with patch("soctalk.workers.dfir_iris.get_dfir_iris_client", return_value=client), \
         patch("soctalk.workers.dfir_iris.emit_replay"):
        result = await dfir_iris_worker_node(state)
    assert result.get("last_error") is None
