"""Unit tests for Velociraptor worker node — DB-free, MCP client mocked."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from soctalk.workers.velociraptor import (
    velociraptor_worker_node,
    _resolve_client_ids,
    _get_processes,
    _get_network_connections,
)


def _make_client(tool_results: dict) -> AsyncMock:
    client = AsyncMock()
    async def call_tool(name, params=None):
        return tool_results.get(name, "[]")
    client.call_tool = call_tool
    return client


def _base_state(**kwargs) -> dict:
    state = {
        "investigation": {
            "alerts": [{"source": {"agent_name": "win-endpoint-01"}}],
            "findings": [],
            "metadata": {},
        },
        "supervisor_decision": {"specific_instructions": ""},
    }
    state.update(kwargs)
    return state


@pytest.mark.asyncio
async def test_resolve_client_ids_returns_id():
    rows = json.dumps([{"client_id": "C.abc123", "hostname": "win-endpoint-01"}])
    client = _make_client({"list_clients": rows})
    ids = await _resolve_client_ids(client, _base_state())
    assert ids == ["C.abc123"]


@pytest.mark.asyncio
async def test_resolve_client_ids_no_alerts_returns_empty():
    client = _make_client({})
    state = _base_state()
    state["investigation"]["alerts"] = []
    assert await _resolve_client_ids(client, state) == []


@pytest.mark.asyncio
async def test_get_processes_suspicious_creates_finding():
    clients_rows = json.dumps([{"client_id": "C.abc"}])
    procs_rows = json.dumps([
        {"Name": "powershell.exe", "Pid": 1234, "Ppid": 100,
         "CommandLine": "powershell -enc abc", "Username": "SYSTEM"}
    ])
    client = _make_client({
        "list_clients": clients_rows,
        "client_processes": procs_rows,
    })
    result = await _get_processes(client, _base_state())
    findings = result["investigation"]["findings"]
    assert len(findings) == 1
    assert "Suspicious processes" in findings[0]["description"]


@pytest.mark.asyncio
async def test_get_processes_clean_no_finding():
    clients_rows = json.dumps([{"client_id": "C.abc"}])
    procs_rows = json.dumps([
        {"Name": "chrome.exe", "Pid": 999, "Ppid": 1,
         "CommandLine": "chrome --profile", "Username": "user"}
    ])
    client = _make_client({
        "list_clients": clients_rows,
        "client_processes": procs_rows,
    })
    result = await _get_processes(client, _base_state())
    assert result["investigation"]["findings"] == []


@pytest.mark.asyncio
async def test_get_network_connections_suspicious_port_creates_finding():
    clients_rows = json.dumps([{"client_id": "C.abc"}])
    netstat_rows = json.dumps([
        {"Name": "malware.exe", "Pid": 666,
         "local_ip": "192.168.1.5", "local_port": 54321,
         "remote_ip": "8.8.8.8", "remote_port": 4444,
         "Status": "ESTABLISHED"}
    ])
    client = _make_client({
        "list_clients": clients_rows,
        "client_network_connections": netstat_rows,
    })
    result = await _get_network_connections(client, _base_state())
    findings = result["investigation"]["findings"]
    assert len(findings) == 1
    assert "network connections" in findings[0]["description"]


@pytest.mark.asyncio
async def test_worker_node_success():
    clients_rows = json.dumps([{"client_id": "C.abc"}])
    client = _make_client({
        "list_clients": clients_rows,
        "client_processes": "[]",
        "client_network_connections": "[]",
    })
    with patch("soctalk.workers.velociraptor.get_velociraptor_client", return_value=client), \
         patch("soctalk.workers.velociraptor.emit_replay"):
        result = await velociraptor_worker_node(_base_state())
    assert result.get("last_error") is None


@pytest.mark.asyncio
async def test_worker_node_client_error_completes_gracefully():
    """gRPC errors per-host are caught as warnings — worker does not fail."""
    client = AsyncMock()
    client.call_tool = AsyncMock(side_effect=RuntimeError("gRPC error"))
    with patch("soctalk.workers.velociraptor.get_velociraptor_client", return_value=client), \
         patch("soctalk.workers.velociraptor.emit_replay"):
        result = await velociraptor_worker_node(_base_state())
    assert result.get("last_error") is None
    assert result["investigation"]["findings"] == []


@pytest.mark.asyncio
async def test_worker_node_process_instruction_routes_correctly():
    clients_rows = json.dumps([{"client_id": "C.abc"}])
    client = _make_client({
        "list_clients": clients_rows,
        "client_processes": "[]",
    })
    state = _base_state()
    state["supervisor_decision"] = {"specific_instructions": "get processes"}
    with patch("soctalk.workers.velociraptor.get_velociraptor_client", return_value=client), \
         patch("soctalk.workers.velociraptor.emit_replay"):
        result = await velociraptor_worker_node(state)
    assert result.get("last_error") is None
