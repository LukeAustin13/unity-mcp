"""
Configuration settings for the MCP for Unity Server.
This file contains all configurable parameters for the server.
"""

import ipaddress
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

    # Allow non-remote-hosted HTTP to bind a non-loopback host without auth.
    # Off by default: exposing an unauthenticated bridge to the network lets
    # anyone who can reach it control the Unity Editor. See validate_http_bind.
    allow_insecure_http: bool = False

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


def is_loopback_host(host: str) -> bool:
    """True only for hosts that are NOT reachable from the network.

    Parses IP literals (so 127.0.0.0/8 and ::1 are recognised) and treats
    'localhost' as the only allowed loopback *name*. A non-IP hostname such as
    '127.evil.example' is NOT loopback — matching it on a string prefix would let
    DNS/hosts point the "loopback" name at a routable interface. 0.0.0.0 / :: are
    NOT loopback — they bind every interface, the exact footgun this guards against.
    """
    h = (host or "").strip()
    if h.startswith("[") and h.endswith("]"):  # bracketed IPv6 literal
        h = h[1:-1]
    if h.lower().rstrip(".") == "localhost":
        return True
    try:
        return ipaddress.ip_address(h.rstrip(".")).is_loopback
    except ValueError:
        return False


def validate_http_bind(
    *, transport: str, remote_hosted: bool, host: str, allow_insecure: bool
) -> str | None:
    """Return an error message if this HTTP bind is unsafe, else None.

    Non-remote-hosted HTTP has no authentication, so it must bind a loopback
    host. Remote-hosted mode is exempt (it enforces API-key auth). A non-loopback
    bind is refused unless the operator explicitly opts in via allow_insecure.
    """
    if (transport or "").lower() != "http":
        return None
    if remote_hosted:
        return None
    if is_loopback_host(host):
        return None
    if allow_insecure:
        return None
    return (
        f"Refusing to start: HTTP transport is bound to non-loopback host "
        f"'{host}' with no authentication — anyone who can reach this host:port "
        "could control the Unity Editor. Fix one of these: bind a loopback host "
        "(127.0.0.1); run remote-hosted mode with API-key auth "
        "(--http-remote-hosted --api-key-validation-url ...); or, only on a "
        "trusted network you control, acknowledge the risk with "
        "UNITY_MCP_ALLOW_INSECURE_HTTP=1 (or --allow-insecure-http)."
    )
