"""MCP client infrastructure for connecting to MCP servers."""

from soctalk.mcp.client import MCPClient, MCPClientManager
from soctalk.mcp.bindings import (
    bind_clients,
    cleanup_clients,
    get_manager,
    # Getters
    get_wazuh_client,
    get_cortex_client,
    get_thehive_client,
    get_misp_client,
    get_velociraptor_client,
    get_dfir_iris_client,
    # is_*_enabled helpers
    is_wazuh_enabled,
    is_cortex_enabled,
    is_thehive_enabled,
    is_misp_enabled,
    is_velociraptor_enabled,
    is_dfir_iris_enabled,
    # Utility
    get_enabled_integrations,
)

__all__ = [
    # Core client classes
    "MCPClient",
    "MCPClientManager",
    # Lifecycle
    "bind_clients",
    "cleanup_clients",
    "get_manager",
    # Getters
    "get_wazuh_client",
    "get_cortex_client",
    "get_thehive_client",
    "get_misp_client",
    "get_velociraptor_client",
    "get_dfir_iris_client",
    # is_*_enabled helpers
    "is_wazuh_enabled",
    "is_cortex_enabled",
    "is_thehive_enabled",
    "is_misp_enabled",
    "is_velociraptor_enabled",
    "is_dfir_iris_enabled",
    # Utility
    "get_enabled_integrations",
]
