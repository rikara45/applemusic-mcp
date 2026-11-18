"""Shared test fixtures."""

import json
import os
import socket
from unittest.mock import patch

import pytest

_real_socket_connect = socket.socket.connect
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


@pytest.fixture(autouse=True)
def _reset_throttle_state():
    """The 429 marker is module-level process state (it has to be — it outlives a
    single call). Clear it between tests so a throttle asserted in one test can't
    make the next one's genuine "not found" report as rate-limited."""
    from applemusic_mcp import amp_api

    amp_api.reset_throttle_state()
    yield
    amp_api.reset_throttle_state()


@pytest.fixture(autouse=True)
def _block_external_network(request):
    """Hard guarantee: no test outside the explicit live gates (`ui` / `ui_live`)
    may open a real external socket. Apple's API is external, so this proves the
    mocked suite never touches the real account — a stray un-mocked amp-api call
    raises instead of silently hammering the library. Localhost is allowed (the
    auth server tests bind a real loopback socket)."""
    if request.node.get_closest_marker("ui") or request.node.get_closest_marker("ui_live"):
        yield
        return

    def _guard(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOCAL_HOSTS:
            raise RuntimeError(
                f"Blocked a real network connection to {host!r} in a non-live test "
                "— mock the boundary (responses / monkeypatch). Real API calls only "
                "belong in the TEST_API / TEST_UI live gates."
            )
        return _real_socket_connect(self, address, *args, **kwargs)

    with patch.object(socket.socket, "connect", _guard):
        yield


@pytest.fixture(autouse=True)
def _unrestricted_level(monkeypatch):
    """Tests run unrestricted unless they set a permission level themselves, so
    a level exported in the developer's shell can't leak into the suite."""
    monkeypatch.delenv("APPLEMUSIC_LEVEL", raising=False)
    monkeypatch.delenv("APPLEMUSIC_ALLOW_UI", raising=False)


@pytest.fixture(autouse=True)
def _isolate_browser_profile(request, tmp_path, monkeypatch):
    """Never let a non-live test touch the real Chrome profile. `clear_session`
    (hit by logout/reset on both the CLI and the `config` tool) rmtree's
    PROFILE_DIR — point it at a throwaway dir so a test can't wipe the user's
    signed-in browser session. The live gates need the real profile, so they're
    exempt."""
    if request.node.get_closest_marker("ui") or request.node.get_closest_marker("ui_live"):
        yield
        return
    try:
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path / "chrome-profile")
    except Exception:
        pass
    yield


# Tests use file-based token storage (not the OS keychain) for determinism and so
# the fixtures that write token JSON files keep working. The keychain path is
# exercised explicitly in test_auth.py.
os.environ.setdefault("APPLEMUSIC_NO_KEYRING", "1")

# Redirect ALL persistent state (config / Chrome profile / cache / exports) to a
# throwaway home for the whole session, BEFORE importing any applemusic_mcp module
# (the path roots are read at import). This is the filesystem analogue of the
# network guard: a test can never touch the user's real ~/.config,
# ~/.applemusic-mcp, or ~/.cache, no matter what it forgets to mock.
import tempfile as _tempfile

os.environ.setdefault("APPLEMUSIC_MCP_HOME", _tempfile.mkdtemp(prefix="amc-test-home-"))

from applemusic_mcp import audit_log


# Never hit Apple's network to harvest a web token during tests. amp-api's
# resolve_web_token() harvests when nothing is cached; stub the harvest (the
# network boundary) so the real resolver logic still runs but offline. Tests that
# need the real harvest (test_harvest) restore it; tests that control harvesting
# monkeypatch it per-test, which overrides this.
@pytest.fixture(autouse=True)
def _isolate_keychain(monkeypatch):
    """Never touch the user's REAL OS keychain.

    Temp config dirs only cover the file store: ``secret_set``/``secret_delete``
    still reach the keychain when the file path is taken (their best-effort stale
    copy purge), which really did wipe a developer's saved Music-User-Token on
    every `pytest` run. Tests that exercise the keychain install their own fake
    backend, which overrides this."""
    from applemusic_mcp import auth

    monkeypatch.setattr(auth, "_keyring", None)


@pytest.fixture(autouse=True)
def _stub_web_token_harvest():
    from applemusic_mcp import auth

    with patch.object(auth, "harvest_developer_token", return_value="TEST_WEB_TOKEN"):
        yield


# Mock audit log for all tests to avoid polluting real audit log
@pytest.fixture(autouse=True)
def mock_audit_log_for_all_tests(tmp_path):
    """Ensure all tests use a temp audit log, not the real one."""
    audit_dir = tmp_path / ".cache" / "applemusic-mcp"
    audit_dir.mkdir(parents=True)
    log_path = audit_dir / "audit_log.jsonl"
    with patch.object(audit_log, "get_audit_log_path", return_value=log_path):
        yield log_path


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create a temporary config directory."""
    config_dir = tmp_path / ".config" / "applemusic-mcp"
    config_dir.mkdir(parents=True)
    return config_dir


@pytest.fixture
def mock_config_dir(temp_config_dir, monkeypatch):
    """Patch get_config_dir to use temp directory."""
    from applemusic_mcp import auth

    monkeypatch.setattr(auth, "DEFAULT_CONFIG_DIR", temp_config_dir)
    return temp_config_dir


@pytest.fixture
def sample_config():
    """Sample configuration data."""
    return {
        "team_id": "TEST_TEAM_ID",
        "key_id": "TEST_KEY_ID",
        "private_key_path": "~/.config/applemusic-mcp/AuthKey_TEST.p8",
    }


@pytest.fixture
def sample_private_key():
    """Sample EC private key for testing (not a real key)."""
    # This is a test-only key, generated for testing purposes
    return """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgtest1234567890
abcdefghijklmnopqrstuvwxyzABCDEFGHhRANCAARtest1234567890abcdefgh
ijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcdefghi
-----END PRIVATE KEY-----"""


@pytest.fixture
def configured_config_dir(mock_config_dir, sample_config, sample_private_key):
    """Config directory with config.json and private key."""
    # Write config
    config_file = mock_config_dir / "config.json"
    with open(config_file, "w") as f:
        json.dump(sample_config, f)

    # Write fake private key
    key_file = mock_config_dir / "AuthKey_TEST.p8"
    with open(key_file, "w") as f:
        f.write(sample_private_key)

    # Update config to use actual path
    sample_config["private_key_path"] = str(key_file)
    with open(config_file, "w") as f:
        json.dump(sample_config, f)

    return mock_config_dir


@pytest.fixture
def mock_developer_token():
    """A mock developer token."""
    return "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6IlRFU1RfS0VZX0lEIn0.eyJpc3MiOiJURVNUX1RFQU1fSUQiLCJpYXQiOjE3MDAwMDAwMDAsImV4cCI6MTcxNTAwMDAwMH0.test_signature"


@pytest.fixture
def mock_user_token():
    """A mock music user token."""
    return "Atest1234567890abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def mock_api_headers(mock_developer_token, mock_user_token):
    """Mock API headers."""
    return {
        "Authorization": f"Bearer {mock_developer_token}",
        "Music-User-Token": mock_user_token,
        "Content-Type": "application/json",
    }
