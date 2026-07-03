"""
Configuration settings for the MCP for Unity Server.
This file contains all configurable parameters for the server.
"""

from dataclasses import dataclass, field


@dataclass
class ServerConfig:
    """Main configuration class for the MCP server."""

    # Network settings
    unity_host: str = "127.0.0.1"
    unity_port: int = 6400
    mcp_port: int = 6500

    # Transport settings
    transport_mode: str = "stdio"

    # HTTP transport behaviour
    http_remote_hosted: bool = False

    # API key authentication (required when http_remote_hosted=True)
    api_key_validation_url: str | None = None  # POST endpoint to validate keys
    api_key_login_url: str | None = None       # URL for users to get/manage keys
    # Cache TTL in seconds (5 min default)
    api_key_cache_ttl: float = 300.0
    # Optional service token for authenticating to the validation endpoint
    api_key_service_token_header: str | None = None  # e.g. "X-Service-Token"
    api_key_service_token: str | None = None         # The token value

    # Connection settings
    connection_timeout: float = 30.0
    buffer_size: int = 16 * 1024 * 1024  # 16MB buffer

    # STDIO framing behaviour
    require_framing: bool = True
    handshake_timeout: float = 1.0
    framed_receive_timeout: float = 2.0
    max_heartbeat_frames: int = 16
    heartbeat_timeout: float = 2.0

    # Safety mode: read_only | review_only | write (see core/safety.py)
    safety_mode: str = "write"

    # Execution-surface guards (enforced by core/enforcement.py). All default to
    # the safe/off position; a server operator opts in explicitly.
    #  - allow_execute_code: permit the un-sandboxed execute_code tool at all.
    #  - allow_arbitrary_menu_items: permit any execute_menu_item path (not just
    #    the allowlist).
    #  - menu_item_allowlist: extra menu paths to allow on top of the built-in
    #    safe defaults (see core/safety.DEFAULT_MENU_ITEM_ALLOWLIST).
    #  - allow_external_build_output: permit manage_build output paths that are
    #    absolute/external to the project.
    allow_execute_code: bool = False
    allow_arbitrary_menu_items: bool = False
    menu_item_allowlist: list[str] = field(default_factory=list)
    allow_external_build_output: bool = False

    # Logging settings
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Server settings
    max_retries: int = 5
    retry_delay: float = 0.25
    # Backoff hint returned to clients when Unity is reloading (milliseconds)
    reload_retry_ms: int = 250
    # Number of polite retries when Unity reports reloading
    # 40 × 250ms ≈ 10s default window
    reload_max_retries: int = 40

    # Port discovery cache
    port_registry_ttl: float = 5.0

    # Telemetry settings
    # Fork default: OFF. The upstream project defaults telemetry on to a Coplay
    # endpoint; this fork does not silently send usage to upstream infrastructure.
    # Opt in explicitly with UNITY_MCP_TELEMETRY_ENABLED=1 (see core/telemetry.py).
    telemetry_enabled: bool = False
    # Endpoint used only when telemetry is explicitly enabled.
    telemetry_endpoint: str = "https://api-prod.coplay.dev/telemetry/events"


# Create a global config instance
config = ServerConfig()
