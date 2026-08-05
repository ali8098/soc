#!/usr/bin/env python3
"""mcp-server-velociraptor — real MCP server (stdio/JSON-RPC), same protocol
soctalk's MCPClient (mcp/client.py) already speaks to mcp-server-wazuh /
-cortex / -thehive / -misp.

Correction from an earlier draft in this conversation: soctalk does NOT call
some ad-hoc "get_available_tools/get_tool_schema/call_tool" shape directly —
that shape is what the *official MCP SDK's ClientSession* exposes locally
after doing a real MCP handshake with a subprocess over stdio. So this file
has to be an actual MCP server, not a Python object pretending to be one.

Good news: it does NOT need to be Rust. soctalk/mcp/client.py spawns
`self.config.path` as a bare subprocess via StdioServerParameters — any
executable works. Ship this as a `#!/usr/bin/env python3` script, chmod +x,
and point WAZUH-sibling env var at it (VELOCIRAPTOR_MCP_SERVER_PATH below)
exactly like the existing Rust binaries are pointed at via
WAZUH_MCP_SERVER_PATH / CORTEX_MCP_SERVER_PATH / etc.

Auth to the real Velociraptor server uses pyvelociraptor (gRPC) — that part
of the earlier draft was correct and is reused below unchanged.

Install:
    pip install "mcp[cli]" pyvelociraptor grpcio pyyaml
    chmod +x mcp-server-velociraptor.py

Run standalone for a smoke test (bypasses soctalk entirely):
    VELOCIRAPTOR_API_CLIENT_CONFIG=/path/to/api_client.yaml \
        python mcp-server-velociraptor.py
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    from pyvelociraptor import api_pb2, api_pb2_grpc
    import grpc
    import yaml
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "pyvelociraptor/grpcio/pyyaml not installed — "
        "pip install pyvelociraptor grpcio pyyaml"
    ) from e


mcp = FastMCP("velociraptor")

_CONFIG_PATH = os.environ["VELOCIRAPTOR_API_CLIENT_CONFIG"]  # fail loud if unset
_MAX_ROWS = int(os.environ.get("VELOCIRAPTOR_MAX_ROWS", "200"))

with open(_CONFIG_PATH, "r") as f:
    _CREDS = yaml.safe_load(f)


def _channel():
    creds = grpc.ssl_channel_credentials(
        root_certificates=_CREDS["ca_certificate"].encode(),
        private_key=_CREDS["client_private_key"].encode(),
        certificate_chain=_CREDS["client_cert"].encode(),
    )
    return grpc.secure_channel(_CREDS["api_connection_string"], creds)


def _run_vql_sync(vql: str, env_params: dict[str, Any]) -> list[dict[str, Any]]:
    env = [api_pb2.VQLEnv(key=k, value=str(v)) for k, v in env_params.items()]
    request = api_pb2.VQLCollectorArgs(Query=[api_pb2.VQLRequest(VQL=vql)], env=env)
    rows: list[dict[str, Any]] = []
    with _channel() as channel:
        stub = api_pb2_grpc.APIStub(channel)
        for response in stub.Query(request):
            if response.Response:
                rows.extend(json.loads(response.Response))
    return rows


async def _run_vql(vql: str, env_params: dict[str, Any]) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_run_vql_sync, vql, env_params)


# ---------------------------------------------------------------------------
# Tools — canned, parameterized VQL only. Same reasoning as soctalk's own
# read-only DB tool surface: never expose free-form VQL to the model, since
# VQL can execute arbitrary actions on enrolled endpoints.
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_clients(hostname_filter: str = "", limit: int = 50) -> str:
    """List Velociraptor-enrolled endpoints, optionally filtered by hostname substring."""
    rows = await _run_vql(
        "SELECT client_id, os_info.hostname AS hostname, "
        "os_info.system AS os, last_seen_at "
        "FROM clients() "
        "WHERE hostname_filter = '' OR os_info.hostname =~ hostname_filter "
        "LIMIT limit",
        {"hostname_filter": hostname_filter, "limit": min(limit, _MAX_ROWS)},
    )
    return json.dumps(rows, default=str)


@mcp.tool()
async def client_processes(client_id: str, name_filter: str = "") -> str:
    """Running processes on one Velociraptor client (pslist artifact). Use the client_id from list_clients."""
    rows = await _run_vql(
        "SELECT Name, Pid, Ppid, CommandLine, Username "
        "FROM Artifact.Windows.System.Pslist(client_id=client_id) "
        "WHERE name_filter = '' OR Name =~ name_filter",
        {"client_id": client_id, "name_filter": name_filter},
    )
    return json.dumps(rows[:_MAX_ROWS], default=str)


@mcp.tool()
async def hunt_results(hunt_id: str, limit: int = 100) -> str:
    """Fetch results of a completed Velociraptor hunt by hunt_id."""
    rows = await _run_vql(
        "SELECT * FROM hunt_results(hunt_id=hunt_id, artifact='') LIMIT limit",
        {"hunt_id": hunt_id, "limit": min(limit, _MAX_ROWS)},
    )
    return json.dumps(rows, default=str)


@mcp.tool()
async def client_network_connections(client_id: str) -> str:
    """Active network connections on one client (Netstat artifact) — for confirming C2 beacons."""
    rows = await _run_vql(
        "SELECT Pid, Name, Laddr.IP AS local_ip, Laddr.Port AS local_port, "
        "Raddr.IP AS remote_ip, Raddr.Port AS remote_port, Status "
        "FROM Artifact.Generic.Network.Netstat(client_id=client_id)",
        {"client_id": client_id},
    )
    return json.dumps(rows[:_MAX_ROWS], default=str)


if __name__ == "__main__":
    mcp.run(transport="stdio")
