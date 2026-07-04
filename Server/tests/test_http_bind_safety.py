"""Tests for the HTTP-bind safe default (refuse unauthenticated network exposure)."""
import pytest

from core.config import is_loopback_host, validate_http_bind


class TestIsLoopback:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.5.5.5", "LOCALHOST"])
    def test_loopback(self, host):
        assert is_loopback_host(host) is True

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "10.0.0.5", "example.com", ""])
    def test_not_loopback(self, host):
        assert is_loopback_host(host) is False

    @pytest.mark.parametrize("host", [
        "127.evil.example",        # hostname whose first label is '127.'
        "127.0.0.1.attacker.com",  # loopback-looking hostname
        "localhost.evil.com",      # not the localhost name
        "0.0.0.0.localhost",       # not a real loopback name/IP
    ])
    def test_hostname_lookalikes_are_not_loopback(self, host):
        # A hostname that merely looks loopback-ish must NOT skip the bind guard
        # (only real IP literals and the exact name 'localhost' count).
        assert is_loopback_host(host) is False

    def test_refuses_loopback_lookalike_hostname(self):
        assert validate_http_bind(
            transport="http", remote_hosted=False, host="127.evil.example", allow_insecure=False
        ) is not None


class TestValidateHttpBind:
    def test_stdio_never_refused(self):
        assert validate_http_bind(transport="stdio", remote_hosted=False, host="0.0.0.0", allow_insecure=False) is None

    def test_loopback_http_ok(self):
        assert validate_http_bind(transport="http", remote_hosted=False, host="127.0.0.1", allow_insecure=False) is None

    def test_nonloopback_local_http_refused(self):
        msg = validate_http_bind(transport="http", remote_hosted=False, host="0.0.0.0", allow_insecure=False)
        assert msg is not None
        assert "0.0.0.0" in msg
        assert "no authentication" in msg

    def test_nonloopback_lan_ip_refused(self):
        assert validate_http_bind(transport="http", remote_hosted=False, host="192.168.1.20", allow_insecure=False) is not None

    def test_remote_hosted_exempt(self):
        # Remote-hosted mode enforces API-key auth, so a non-loopback bind is allowed.
        assert validate_http_bind(transport="http", remote_hosted=True, host="0.0.0.0", allow_insecure=False) is None

    def test_explicit_optin_allows(self):
        assert validate_http_bind(transport="http", remote_hosted=False, host="0.0.0.0", allow_insecure=True) is None
