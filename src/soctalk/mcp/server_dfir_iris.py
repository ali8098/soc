#!/usr/bin/env python3
"""mcp-server-dfir-iris — real MCP server (stdio/JSON-RPC), same protocol
soctalk's MCPClient (mcp/client.py) speaks to mcp-server-wazuh /
-cortex / -thehive / -misp / -velociraptor.

Auth to DFIR-IRIS uses bearer-token REST (httpx) — simpler than
Velociraptor's mTLS/gRPC. Set DFIR_IRIS_URL and DFIR_IRIS_API_KEY.

Install:
    pip install "mcp[cli]" httpx
    chmod +x mcp-server-dfir-iris.py

Run standalone for a smoke test:
    DFIR_IRIS_URL=https://iris.example.com \
    DFIR_IRIS_API_KEY=your-api-key \
        python mcp-server-dfir-iris.py
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    import httpx
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "httpx not installed — pip install httpx"
    ) from e


mcp = FastMCP("dfir-iris")

_BASE_URL = os.environ["DFIR_IRIS_URL"].rstrip("/")   # fail loud if unset
_API_KEY  = os.environ["DFIR_IRIS_API_KEY"]           # fail loud if unset
_VERIFY_SSL = os.environ.get("DFIR_IRIS_VERIFY_SSL", "true").lower() != "false"
_MAX_ROWS = int(os.environ.get("DFIR_IRIS_MAX_ROWS", "200"))

_HEADERS = {
    "Authorization": f"Bearer {_API_KEY}",
    "Content-Type": "application/json",
}


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=_BASE_URL,
        headers=_HEADERS,
        verify=_VERIFY_SSL,
        timeout=30,
    )


def _get_sync(path: str, params: dict[str, Any] | None = None) -> Any:
    with _client() as c:
        r = c.get(path, params=params)
        r.raise_for_status()
        return r.json()


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    return await asyncio.to_thread(_get_sync, path, params)


def _post_sync(path: str, body: dict[str, Any]) -> Any:
    with _client() as c:
        r = c.post(path, json=body)
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict[str, Any]) -> Any:
    return await asyncio.to_thread(_post_sync, path, body)


# ---------------------------------------------------------------------------
# Tools — read-only surface matching soctalk's philosophy.
# Never expose mutation endpoints (create/delete/update) to the model.
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_cases(
    status_filter: str = "",
    limit: int = 50,
) -> str:
    """List DFIR-IRIS cases (investigations). 
    
    Args:
        status_filter: Optional status to filter by e.g. 'Open', 'Closed'.
        limit: Max number of cases to return (default 50, max 200).
    """
    data = await _get("/manage/cases/list")

    cases = data.get("data", {}).get("cases", [])

    if status_filter:
        cases = [
            c for c in cases
            if str(c.get("case_status_name", "")).lower() == status_filter.lower()
        ]

    trimmed = []
    for c in cases[:min(limit, _MAX_ROWS)]:
        trimmed.append({
            "case_id":       c.get("case_id"),
            "case_name":     c.get("case_name"),
            "client_name":   c.get("client_name"),
            "status":        c.get("case_status_name"),
            "severity":      c.get("case_severity_name"),
            "classification":c.get("classification_name"),
            "owner":         c.get("owner_username"),
            "open_date":     c.get("open_date"),
            "close_date":    c.get("close_date"),
        })

    return json.dumps(trimmed, default=str)


@mcp.tool()
async def get_case(case_id: int) -> str:
    """Get full details of a single DFIR-IRIS case by case_id.

    Args:
        case_id: Numeric case ID from list_cases.
    """
    data = await _get(f"/case/summary", params={"cid": case_id})
    return json.dumps(data.get("data", {}), default=str)


@mcp.tool()
async def list_case_iocs(case_id: int) -> str:
    """List IOCs (Indicators of Compromise) attached to a DFIR-IRIS case.

    Args:
        case_id: Numeric case ID from list_cases.
    """
    data = await _get("/case/ioc/list", params={"cid": case_id})
    iocs = data.get("data", {}).get("ioc", [])

    trimmed = []
    for ioc in iocs[:_MAX_ROWS]:
        trimmed.append({
            "ioc_id":          ioc.get("ioc_id"),
            "ioc_value":       ioc.get("ioc_value"),
            "ioc_type":        ioc.get("ioc_type_name"),
            "ioc_tlp":         ioc.get("ioc_tlp_name"),
            "ioc_description": ioc.get("ioc_description"),
            "ioc_tags":        ioc.get("ioc_tags"),
            "added_by":        ioc.get("user_name"),
        })

    return json.dumps(trimmed, default=str)


@mcp.tool()
async def list_case_assets(case_id: int) -> str:
    """List assets (endpoints/users/etc.) linked to a DFIR-IRIS case.

    Args:
        case_id: Numeric case ID from list_cases.
    """
    data = await _get("/case/assets/list", params={"cid": case_id})
    assets = data.get("data", {}).get("assets", [])

    trimmed = []
    for a in assets[:_MAX_ROWS]:
        trimmed.append({
            "asset_id":          a.get("asset_id"),
            "asset_name":        a.get("asset_name"),
            "asset_type":        a.get("asset_type_name"),
            "asset_ip":          a.get("asset_ip"),
            "asset_description": a.get("asset_description"),
            "asset_compromise":  a.get("asset_compromise_status_name"),
            "linked_iocs":       a.get("linked_ioc", []),
        })

    return json.dumps(trimmed, default=str)


@mcp.tool()
async def list_case_timeline(case_id: int, limit: int = 100) -> str:
    """Get the event timeline for a DFIR-IRIS case.

    Args:
        case_id: Numeric case ID from list_cases.
        limit: Max events to return (default 100, max 200).
    """
    data = await _get("/case/timeline/events/list", params={"cid": case_id})
    events = data.get("data", {}).get("timeline", [])

    trimmed = []
    for e in events[:min(limit, _MAX_ROWS)]:
        trimmed.append({
            "event_id":          e.get("event_id"),
            "event_date":        e.get("event_date"),
            "event_title":       e.get("event_title"),
            "event_category":    e.get("event_category_name"),
            "event_content":     e.get("event_content"),
            "event_source":      e.get("event_source"),
            "event_tags":        e.get("event_tags"),
            "linked_assets":     e.get("linked_assets", []),
            "linked_iocs":       e.get("linked_ioc", []),
        })

    return json.dumps(trimmed, default=str)


@mcp.tool()
async def list_case_notes(case_id: int) -> str:
    """List investigation notes for a DFIR-IRIS case.

    Args:
        case_id: Numeric case ID from list_cases.
    """
    data = await _get("/case/notes/list", params={"cid": case_id})
    groups = data.get("data", {}).get("notes_groups", [])

    results = []
    for group in groups:
        for note in group.get("notes", [])[:_MAX_ROWS]:
            results.append({
                "note_id":        note.get("note_id"),
                "note_title":     note.get("note_title"),
                "note_content":   note.get("note_content"),
                "group_title":    group.get("group_title"),
                "last_update":    note.get("note_lastupdate"),
                "updated_by":     note.get("user_name"),
            })

    return json.dumps(results[:_MAX_ROWS], default=str)


@mcp.tool()
async def search_iocs(ioc_value: str) -> str:
    """Search for an IOC value across ALL cases in DFIR-IRIS.

    Useful for pivot — given an IP/domain/hash, find every case it appears in.

    Args:
        ioc_value: The IOC value to search for (IP, domain, hash, etc).
    """
    data = await _post("/manage/iocs/filter/by-values", {"values": [ioc_value]})
    iocs = data.get("data", [])

    trimmed = []
    for ioc in iocs[:_MAX_ROWS]:
        trimmed.append({
            "ioc_value":   ioc.get("ioc_value"),
            "ioc_type":    ioc.get("ioc_type_name"),
            "case_id":     ioc.get("case_id"),
            "case_name":   ioc.get("case_name"),
            "ioc_tlp":     ioc.get("ioc_tlp_name"),
            "added_by":    ioc.get("user_name"),
        })

    return json.dumps(trimmed, default=str)


if __name__ == "__main__":
    mcp.run(transport="stdio")
