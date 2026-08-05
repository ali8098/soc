"""Velociraptor worker node for endpoint hunting and forensics."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import structlog

from soctalk.core.ir import replay_events
from soctalk.graph.event_sink import emit as emit_replay
from soctalk.mcp.bindings import get_velociraptor_client
from soctalk.models.enums import Severity
from soctalk.models.investigation import Finding

logger = structlog.get_logger()


async def velociraptor_worker_node(state: dict[str, Any]) -> dict[str, Any]:
    """Velociraptor worker node — endpoint hunting and live forensics.

    Capabilities:
    - List enrolled clients by hostname
    - Get running processes (cross-platform)
    - Get active network connections (C2 beacon detection)
    - Fetch completed hunt results

    Args:
        state: Current graph state.

    Returns:
        Updated state dictionary.
    """
    logger.info("velociraptor_worker_started")
    emit_replay(replay_events.worker_started("velociraptor", action="HUNT"))

    client = get_velociraptor_client()
    supervisor_decision = state.get("supervisor_decision", {})
    specific_instructions = (
        (supervisor_decision.get("specific_instructions") or "")
        if supervisor_decision
        else ""
    )

    try:
        instructions_lower = specific_instructions.lower()

        if "process" in instructions_lower or "pslist" in instructions_lower:
            state = await _get_processes(client, state)
        elif "network" in instructions_lower or "connection" in instructions_lower or "c2" in instructions_lower:
            state = await _get_network_connections(client, state)
        elif "hunt" in instructions_lower:
            state = await _get_hunt_results(client, state, specific_instructions)
        else:
            # Default: find client by hostname then get processes + connections
            state = await _full_endpoint_triage(client, state)

        state["last_error"] = None
        logger.info("velociraptor_worker_completed")

    except Exception as e:
        logger.error("velociraptor_worker_error", error=str(e))
        state["last_error"] = f"Velociraptor worker error: {str(e)}"
        state["error_count"] = state.get("error_count", 0) + 1

    state["last_updated"] = datetime.now().isoformat()
    emit_replay(
        replay_events.worker_result(
            "velociraptor",
            ok=state.get("last_error") is None,
            summary=state.get("last_error"),
        )
    )
    return state


async def _resolve_client_ids(client: Any, state: dict[str, Any]) -> list[str]:
    """Resolve hostnames from alert agents to Velociraptor client_ids.

    Args:
        client: Velociraptor MCP client.
        state: Current state.

    Returns:
        List of client_ids (up to 3).
    """
    investigation = state.get("investigation", {})
    alerts = investigation.get("alerts", [])

    hostnames: set[str] = set()
    for alert in alerts:
        source = alert.get("source", {})
        agent_name = source.get("agent_name")
        if agent_name and agent_name != "unknown":
            hostnames.add(agent_name)

    client_ids: list[str] = []
    for hostname in list(hostnames)[:3]:
        try:
            result = await client.call_tool(
                "list_clients", {"hostname_filter": hostname, "limit": 1}
            )
            rows = json.loads(result) if isinstance(result, str) else result
            if rows:
                cid = rows[0].get("client_id")
                if cid:
                    client_ids.append(cid)
                    logger.info("velociraptor_client_resolved", hostname=hostname, client_id=cid)
        except Exception as e:
            logger.warning("velociraptor_client_resolve_failed", hostname=hostname, error=str(e))

    return client_ids


async def _get_processes(client: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Get running processes for alert hosts.

    Args:
        client: Velociraptor MCP client.
        state: Current state.

    Returns:
        Updated state with process findings.
    """
    client_ids = await _resolve_client_ids(client, state)
    if not client_ids:
        logger.info("velociraptor_no_clients_for_processes")
        return state

    investigation = state.get("investigation", {})
    findings = investigation.get("findings", [])
    metadata = investigation.get("metadata", {})

    suspicious_patterns = [
        "powershell", "cmd.exe", "wscript", "cscript", "mshta",
        "certutil", "bitsadmin", "regsvr32", "rundll32",
        "mimikatz", "procdump", "psexec", "nc", "ncat",
    ]

    for cid in client_ids:
        try:
            result = await client.call_tool("client_processes", {"client_id": cid})
            rows = json.loads(result) if isinstance(result, str) else result

            # Store raw result in metadata
            metadata.setdefault("velociraptor_processes", {})[cid] = rows

            # Look for suspicious processes
            suspicious = [
                r for r in rows
                if any(p in (r.get("Name") or "").lower() for p in suspicious_patterns)
            ]
            if suspicious:
                finding = Finding(
                    description=f"Suspicious processes found on client {cid}",
                    severity=Severity.HIGH,
                    evidence=[
                        f"{r.get('Name')} (PID {r.get('Pid')}) — {r.get('CommandLine', '')[:120]}"
                        for r in suspicious[:5]
                    ],
                    recommendations=[
                        "Review process command lines",
                        "Check parent process chain",
                        "Correlate with network connections",
                    ],
                )
                findings.append(finding.model_dump())
                logger.info("velociraptor_suspicious_processes", client_id=cid, count=len(suspicious))

        except Exception as e:
            logger.warning("velociraptor_processes_failed", client_id=cid, error=str(e))

    investigation["findings"] = findings
    investigation["metadata"] = metadata
    state["investigation"] = investigation
    return state


async def _get_network_connections(client: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Get active network connections for alert hosts.

    Args:
        client: Velociraptor MCP client.
        state: Current state.

    Returns:
        Updated state with network connection findings.
    """
    client_ids = await _resolve_client_ids(client, state)
    if not client_ids:
        logger.info("velociraptor_no_clients_for_netstat")
        return state

    investigation = state.get("investigation", {})
    findings = investigation.get("findings", [])
    metadata = investigation.get("metadata", {})

    # Known-bad / suspicious ports (common C2 patterns)
    suspicious_ports = {4444, 1234, 31337, 8888, 9999, 5555, 6666, 7777}
    private_prefixes = ("10.", "192.168.", "172.")

    for cid in client_ids:
        try:
            result = await client.call_tool(
                "client_network_connections", {"client_id": cid}
            )
            rows = json.loads(result) if isinstance(result, str) else result

            metadata.setdefault("velociraptor_netstat", {})[cid] = rows

            # Flag suspicious outbound connections:
            # - known bad ports (C2 common ports), OR
            # - established connections to non-RFC1918 (public) IPs
            suspicious = [
                r for r in rows
                if (
                    r.get("remote_port") in suspicious_ports
                    or (
                        not r.get("remote_ip", "").startswith(private_prefixes)
                        and r.get("Status") == "ESTABLISHED"
                    )
                )
            ]
            if suspicious:
                finding = Finding(
                    description=f"Suspicious network connections on client {cid}",
                    severity=Severity.HIGH,
                    evidence=[
                        f"{r.get('Name')} (PID {r.get('Pid')}) → "
                        f"{r.get('remote_ip')}:{r.get('remote_port')} [{r.get('Status')}]"
                        for r in suspicious[:5]
                    ],
                    recommendations=[
                        "Block suspicious remote IPs at perimeter",
                        "Isolate endpoint if C2 confirmed",
                    ],
                )
                findings.append(finding.model_dump())
                logger.info("velociraptor_suspicious_connections", client_id=cid, count=len(suspicious))

        except Exception as e:
            logger.warning("velociraptor_netstat_failed", client_id=cid, error=str(e))

    investigation["findings"] = findings
    investigation["metadata"] = metadata
    state["investigation"] = investigation
    return state


async def _get_hunt_results(
    client: Any, state: dict[str, Any], instructions: str
) -> dict[str, Any]:
    """Fetch results of a specific hunt mentioned in instructions.

    Args:
        client: Velociraptor MCP client.
        state: Current state.
        instructions: Supervisor instructions (should contain hunt_id).

    Returns:
        Updated state with hunt results.
    """
    # Extract hunt_id from instructions e.g. "get hunt results H.ABC123"
    match = re.search(r"H\.[A-Za-z0-9]+", instructions)
    if not match:
        logger.warning("velociraptor_no_hunt_id_in_instructions")
        return state

    hunt_id = match.group(0)
    investigation = state.get("investigation", {})
    metadata = investigation.get("metadata", {})

    try:
        result = await client.call_tool("hunt_results", {"hunt_id": hunt_id, "limit": 100})
        rows = json.loads(result) if isinstance(result, str) else result
        metadata["velociraptor_hunt_results"] = {hunt_id: rows}
        investigation["metadata"] = metadata
        state["investigation"] = investigation
        logger.info("velociraptor_hunt_results_fetched", hunt_id=hunt_id, rows=len(rows))
    except Exception as e:
        logger.warning("velociraptor_hunt_failed", hunt_id=hunt_id, error=str(e))

    return state


async def _full_endpoint_triage(client: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Default: processes + network connections for all alert hosts.

    Args:
        client: Velociraptor MCP client.
        state: Current state.

    Returns:
        Updated state.
    """
    state = await _get_processes(client, state)
    state = await _get_network_connections(client, state)
    return state
