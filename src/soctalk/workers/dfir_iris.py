"""DFIR-IRIS worker node for case management queries."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog

from soctalk.core.ir import replay_events
from soctalk.graph.event_sink import emit as emit_replay
from soctalk.mcp.bindings import get_dfir_iris_client
from soctalk.models.enums import Severity
from soctalk.models.investigation import Finding

logger = structlog.get_logger()


async def dfir_iris_worker_node(state: dict[str, Any]) -> dict[str, Any]:
    """DFIR-IRIS worker — queries case management for prior incident context."""
    logger.info("dfir_iris_worker_started")
    emit_replay(replay_events.worker_started("dfir_iris", action="QUERY_IRIS"))

    client = get_dfir_iris_client()
    supervisor_decision = state.get("supervisor_decision", {})
    specific_instructions = (
        (supervisor_decision.get("specific_instructions") or "")
        if supervisor_decision
        else ""
    )

    try:
        instructions_lower = specific_instructions.lower()

        if any(k in instructions_lower for k in ("ioc", "pivot", "search")):
            state = await _search_iocs_from_alert(client, state)
        elif "timeline" in instructions_lower:
            state = await _get_case_timeline(client, state)
        elif "asset" in instructions_lower:
            state = await _get_case_assets(client, state)
        else:
            # Default: pivot on alert observables, fall back to open-case list
            state = await _search_iocs_from_alert(client, state)
            hits = (state.get("investigation") or {}).get("metadata", {}).get(
                "dfir_iris_ioc_pivot", []
            )
            if not hits:
                state = await _find_related_cases(client, state)

        state["last_error"] = None
        logger.info("dfir_iris_worker_completed")

    except Exception as e:
        logger.error("dfir_iris_worker_error", error=str(e))
        state["last_error"] = f"DFIR-IRIS worker error: {str(e)}"
        state["error_count"] = state.get("error_count", 0) + 1

    state["last_updated"] = datetime.now().isoformat()
    emit_replay(
        replay_events.worker_result(
            "dfir_iris",
            ok=state.get("last_error") is None,
            summary=state.get("last_error"),
        )
    )
    return state


async def _find_related_cases(client: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Fetch recent open cases as background context."""
    result = await client.call_tool("list_cases", {"status_filter": "Open", "limit": 20})
    investigation = state.get("investigation", {})
    metadata = investigation.get("metadata", {})
    metadata["dfir_iris_open_cases"] = result
    investigation["metadata"] = metadata
    state["investigation"] = investigation
    logger.info("dfir_iris_open_cases_fetched")
    return state


async def _search_iocs_from_alert(client: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Pivot on alert observables — search each across all DFIR-IRIS cases."""
    investigation = state.get("investigation", {})
    alerts = investigation.get("alerts", [])
    findings = investigation.get("findings", [])

    observables: set[str] = set()
    for alert in alerts:
        for obs in alert.get("observables", []):
            val = obs.get("value")
            if val:
                observables.add(val)

    if not observables:
        logger.info("dfir_iris_ioc_pivot_no_observables")
        return state

    hits: list[dict] = []
    for ioc_value in list(observables)[:10]:
        try:
            result = await client.call_tool("search_iocs", {"ioc_value": ioc_value})
            if result and result != "[]":
                hits.append({"ioc": ioc_value, "cases": result})
                logger.info("dfir_iris_ioc_pivot_hit", ioc=ioc_value)
        except Exception as e:
            logger.warning("dfir_iris_ioc_pivot_error", ioc=ioc_value, error=str(e))

    if hits:
        finding = Finding(
            description=f"DFIR-IRIS IOC pivot: {len(hits)} observable(s) matched prior cases",
            severity=Severity.HIGH,
            evidence=[str(h) for h in hits[:5]],
            recommendations=[
                "Review matched prior cases for attacker TTPs",
                "Cross-reference timeline with current alert",
            ],
        )
        findings.append(finding.model_dump())
        investigation["findings"] = findings

    metadata = investigation.get("metadata", {})
    metadata["dfir_iris_ioc_pivot"] = hits
    investigation["metadata"] = metadata
    state["investigation"] = investigation
    return state


async def _get_case_timeline(client: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Pull timeline for the first matched DFIR-IRIS case."""
    investigation = state.get("investigation", {})
    metadata = investigation.get("metadata", {})
    case_id = _extract_case_id(metadata.get("dfir_iris_ioc_pivot", []))
    if not case_id:
        logger.info("dfir_iris_timeline_no_case_id")
        return state
    result = await client.call_tool(
        "list_case_timeline", {"case_id": case_id, "limit": 50}
    )
    metadata["dfir_iris_timeline"] = result
    investigation["metadata"] = metadata
    state["investigation"] = investigation
    logger.info("dfir_iris_timeline_fetched", case_id=case_id)
    return state


async def _get_case_assets(client: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Pull assets for the first matched DFIR-IRIS case."""
    investigation = state.get("investigation", {})
    metadata = investigation.get("metadata", {})
    case_id = _extract_case_id(metadata.get("dfir_iris_ioc_pivot", []))
    if not case_id:
        logger.info("dfir_iris_assets_no_case_id")
        return state
    result = await client.call_tool("list_case_assets", {"case_id": case_id})
    metadata["dfir_iris_assets"] = result
    investigation["metadata"] = metadata
    state["investigation"] = investigation
    logger.info("dfir_iris_assets_fetched", case_id=case_id)
    return state


def _extract_case_id(pivot_hits: list[dict]) -> int | None:
    """Extract first case_id from IOC pivot results."""
    for hit in pivot_hits:
        try:
            cases = json.loads(hit.get("cases", "[]"))
            if cases and isinstance(cases, list):
                cid = cases[0].get("case_id")
                if cid:
                    return int(cid)
        except Exception:
            continue
    return None
