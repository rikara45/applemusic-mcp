"""Coverage tests for config tool, snapshot subsystem, and auth actions.

Target lines: 6066-6760 in server.py (config dispatcher, _config_auth_status,
snapshot helpers, _clear_credentials, _auth_action).

Existing tests already cover:
  - set-pref mode/playback valid/invalid (TestModePreference)
  - auth status basic (TestCheckAuthStatus)
  - signin success/still-waiting, logout, reset basics (TestAuthTool)
  - unknown top-level action (TestAuthTool.test_unknown_action)

This file fills the remaining gaps.
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from applemusic_mcp import server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_tokens(d: Path, dev: str, user: str, expires_offset: float = 86400 * 60) -> None:
    """Write developer + user token files into config dir d."""
    (d / "developer_token.json").write_text(
        json.dumps({"token": dev, "expires": time.time() + expires_offset})
    )
    (d / "music_user_token.json").write_text(json.dumps({"music_user_token": user}))


# ---------------------------------------------------------------------------
# set-pref — gap branches
# ---------------------------------------------------------------------------


class TestSetPrefGaps:
    """Branches in set-pref not covered by TestModePreference."""

    def test_no_preference_param(self, mock_config_dir):
        """Missing 'preference' param → error with valid list."""
        out = server.config(action="set-pref")
        assert "requires 'preference' parameter" in out
        assert "mode" in out

    def test_invalid_preference_name(self, mock_config_dir):
        """Unknown preference name → error listing all valid names."""
        out = server.config(action="set-pref", preference="turbo_mode")
        assert "preference must be one of" in out
        assert "mode" in out and "fetch_explicit" in out

    def test_bool_pref_without_value(self, mock_config_dir):
        """Bool pref without value=True/False → error."""
        out = server.config(action="set-pref", preference="fetch_explicit")
        assert "requires 'value' parameter" in out

    def test_bool_pref_set_true(self, mock_config_dir):
        """Bool pref with value=True writes to config."""
        out = server.config(action="set-pref", preference="fetch_explicit", value=True)
        assert "fetch_explicit = True" in out

    def test_bool_pref_clean_only(self, mock_config_dir):
        """clean_only bool pref."""
        out = server.config(action="set-pref", preference="clean_only", value=False)
        assert "clean_only = False" in out

    def test_bool_pref_auto_add(self, mock_config_dir):
        """auto_add bool pref."""
        out = server.config(action="set-pref", preference="auto_add", value=True)
        assert "auto_add = True" in out

    def test_string_pref_storefront_missing_value(self, mock_config_dir):
        """storefront (non-enum string pref) without string_value → error with hint."""
        out = server.config(action="set-pref", preference="storefront")
        assert "requires 'string_value' parameter" in out
        # storefront is not in enum_prefs, so hint is "e.g., string_value='gb'"
        assert "string_value" in out

    def test_string_pref_storefront_valid(self, mock_config_dir):
        """storefront with a string_value sets the pref."""
        out = server.config(action="set-pref", preference="storefront", string_value="GB")
        # lowercased on save
        assert "storefront = gb" in out

    def test_secure_storage_pref_removed(self, mock_config_dir):
        """secure_storage is no longer a user pref (auto by platform) → rejected."""
        out = server.config(action="set-pref", preference="secure_storage", string_value="keychain")
        assert "preference must be one of" in out and "secure_storage" not in out.split(":")[-1]

    def test_set_mode_web(self, mock_config_dir):
        out = server.config(action="set-pref", preference="mode", string_value="web")
        assert "mode = web" in out


# ---------------------------------------------------------------------------
# list-storefronts
# ---------------------------------------------------------------------------


class TestListStorefronts:
    def test_list_storefronts_success(self, mock_config_dir, mock_developer_token, mock_user_token):
        _write_tokens(mock_config_dir, mock_developer_token, mock_user_token)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"id": "us", "attributes": {"name": "United States"}},
                {"id": "gb", "attributes": {"name": "United Kingdom"}},
            ]
        }
        mock_resp.raise_for_status = lambda: None
        with (
            patch.object(server, "get_headers", return_value={}),
            patch("requests.get", return_value=mock_resp),
        ):
            out = server.config(action="list-storefronts")
        assert "United States" in out
        assert "United Kingdom" in out
        assert "Available Storefronts" in out

    def test_list_storefronts_error(self, mock_config_dir):
        with patch.object(server, "get_headers", side_effect=Exception("no token")):
            out = server.config(action="list-storefronts")
        assert "Error listing storefronts" in out


# ---------------------------------------------------------------------------
# clear-tracks
# ---------------------------------------------------------------------------


class TestClearTracks:
    def test_clear_tracks(self, mock_config_dir):
        mock_cache = MagicMock()
        mock_cache._cache = {"a": 1, "b": 2}
        with patch.object(server, "get_track_cache", return_value=mock_cache):
            out = server.config(action="clear-tracks")
        assert "Cleared track metadata cache" in out
        assert "2 entries" in out
        mock_cache.clear.assert_called_once()


# ---------------------------------------------------------------------------
# clear-exports
# ---------------------------------------------------------------------------


class TestClearExports:
    def test_cache_dir_does_not_exist(self, tmp_path):
        nonexistent = tmp_path / "does-not-exist"
        with patch.object(server, "get_cache_dir", return_value=nonexistent):
            out = server.config(action="clear-exports")
        assert "doesn't exist" in out

    def test_no_export_files(self, tmp_path):
        with patch.object(server, "get_cache_dir", return_value=tmp_path):
            out = server.config(action="clear-exports")
        assert "No export files" in out

    def test_clears_all_files(self, tmp_path):
        # Write a couple of export files
        (tmp_path / "export.csv").write_text("a,b,c")
        (tmp_path / "data.json").write_text('{"x":1}')
        # track_cache.json should be skipped
        (tmp_path / "track_cache.json").write_text("{}")
        with patch.object(server, "get_cache_dir", return_value=tmp_path):
            out = server.config(action="clear-exports")
        assert "Deleted" in out
        assert not (tmp_path / "export.csv").exists()
        assert not (tmp_path / "data.json").exists()
        # track_cache.json stays
        assert (tmp_path / "track_cache.json").exists()

    def test_keeps_files_newer_than_days(self, tmp_path):
        # Create a file that is "fresh" (mtime = now)
        f = tmp_path / "fresh.csv"
        f.write_text("data")
        with patch.object(server, "get_cache_dir", return_value=tmp_path):
            out = server.config(action="clear-exports", days_old=30)
        # File is newer than 30 days, so it should be kept
        assert f.exists()
        assert "Kept" in out

    def test_reports_kb_size(self, tmp_path):
        # Write a file large enough to report in KB
        content = "x" * 1025
        (tmp_path / "big.csv").write_text(content)
        with patch.object(server, "get_cache_dir", return_value=tmp_path):
            out = server.config(action="clear-exports")
        assert "KB" in out or "bytes" in out


# ---------------------------------------------------------------------------
# audit-log and clear-audit-log
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_audit_log_returns_entries(self, mock_audit_log_for_all_tests):
        from applemusic_mcp import audit_log as al

        mock_entries = [MagicMock()]
        with (
            patch.object(al, "get_recent_entries", return_value=mock_entries) as mock_get,
            patch.object(al, "format_entries_for_display", return_value="LOG OUTPUT") as mock_fmt,
        ):
            out = server.config(action="audit-log")
        assert "LOG OUTPUT" == out
        mock_get.assert_called_once()
        mock_fmt.assert_called_once()

    def test_clear_audit_log_success(self, mock_audit_log_for_all_tests):
        from applemusic_mcp import audit_log as al

        with (
            patch.object(al, "get_recent_entries", return_value=[1, 2, 3]),
            patch.object(al, "clear_audit_log", return_value=True),
        ):
            out = server.config(action="clear-audit-log")
        assert "Cleared audit log" in out
        assert "3 entries" in out

    def test_clear_audit_log_failure(self, mock_audit_log_for_all_tests):
        from applemusic_mcp import audit_log as al

        with (
            patch.object(al, "get_recent_entries", return_value=[]),
            patch.object(al, "clear_audit_log", return_value=False),
        ):
            out = server.config(action="clear-audit-log")
        assert "Failed to clear" in out


# ---------------------------------------------------------------------------
# info action
# ---------------------------------------------------------------------------


class TestConfigInfo:
    def test_info_basic(self, mock_config_dir, tmp_path):
        # Mock all dependencies so it completes without hitting real disk
        mock_cache = MagicMock()
        mock_cache._cache = {}
        mock_cache.cache_file = tmp_path / "track_cache.json"

        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path),
            patch.object(al, "get_recent_entries", return_value=[]),
            patch.object(al, "get_audit_log_path", return_value=tmp_path / "audit.log"),
        ):
            out = server.config(action="info")

        assert "System Info" in out
        assert "Preferences" in out
        assert "Track Metadata Cache" in out

    def test_info_with_cache_file_exists(self, mock_config_dir, tmp_path):
        """Branch: cache_file.exists() is True (shows size)."""
        mock_cache = MagicMock()
        mock_cache._cache = {"a": 1}
        cache_file = tmp_path / "track_cache.json"
        cache_file.write_text('{"a":1}')
        mock_cache.cache_file = cache_file

        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path),
            patch.object(al, "get_recent_entries", return_value=[]),
            patch.object(al, "get_audit_log_path", return_value=tmp_path / "nope.log"),
        ):
            out = server.config(action="info")
        assert "1 entries" in out

    def test_info_with_export_files(self, mock_config_dir, tmp_path):
        """Branch: export files exist, including MB-size reporting and >10 files."""
        mock_cache = MagicMock()
        mock_cache._cache = {}
        mock_cache.cache_file = tmp_path / "track_cache.json"

        # Write 12 csv files so the ">10 more" branch fires
        for i in range(12):
            f = tmp_path / f"export_{i:02d}.csv"
            f.write_text("data" * 300)  # ~1.2KB each

        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path),
            patch.object(al, "get_recent_entries", return_value=[]),
            patch.object(al, "get_audit_log_path", return_value=tmp_path / "nope.log"),
        ):
            out = server.config(action="info")
        assert "and 2 more" in out

    def test_info_with_audit_log_exists(self, mock_config_dir, tmp_path):
        """Branch: audit log file exists (shows size)."""
        mock_cache = MagicMock()
        mock_cache._cache = {}
        mock_cache.cache_file = tmp_path / "tc.json"

        audit_log_path = tmp_path / "audit.log"
        audit_log_path.write_text("entry1\nentry2\n")

        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path / "empty"),
            patch.object(al, "get_recent_entries", return_value=[1, 2, 3, 4, 5]),
            patch.object(al, "get_audit_log_path", return_value=audit_log_path),
        ):
            out = server.config(action="info")
        assert "Audit Log" in out

    def test_info_large_cache_file_mb(self, mock_config_dir, tmp_path):
        """Branch: cache file >= 1MB → MB suffix."""
        mock_cache = MagicMock()
        mock_cache._cache = {"x": 1}
        cache_file = tmp_path / "track_cache.json"
        # Write >1MB content
        cache_file.write_bytes(b"x" * (1024 * 1024 + 1))
        mock_cache.cache_file = cache_file

        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path / "empty"),
            patch.object(al, "get_recent_entries", return_value=[]),
            patch.object(al, "get_audit_log_path", return_value=tmp_path / "nope.log"),
        ):
            out = server.config(action="info")
        assert "MB" in out

    def test_info_export_file_mb_size(self, mock_config_dir, tmp_path):
        """Branch: export files >= 1MB total → MB total size."""
        mock_cache = MagicMock()
        mock_cache._cache = {}
        mock_cache.cache_file = tmp_path / "tc.json"

        f = tmp_path / "big.json"
        f.write_bytes(b"x" * (1024 * 1024 + 100))

        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path),
            patch.object(al, "get_recent_entries", return_value=[]),
            patch.object(al, "get_audit_log_path", return_value=tmp_path / "nope.log"),
        ):
            out = server.config(action="info")
        assert "MB" in out


# ---------------------------------------------------------------------------
# Exception handler in config dispatcher
# ---------------------------------------------------------------------------


class TestConfigExceptionHandler:
    def test_exception_returns_error_string(self):
        """Force an exception inside config() to hit the except block."""
        # Making get_user_preferences raise covers the broad except at the end
        with patch.object(server, "get_user_preferences", side_effect=RuntimeError("boom")):
            out = server.config(action="info")
        assert "Error" in out and "boom" in out


# ---------------------------------------------------------------------------
# _config_auth_status — coverage of remaining branches
# ---------------------------------------------------------------------------


class TestConfigAuthStatusBranches:
    """Cover branches in _config_auth_status NOT already covered by TestCheckAuthStatus."""

    def test_can_generate_token_branch(self, mock_config_dir, mock_developer_token):
        """Branch: dev_info is not None AND can_generate_developer_token() is True."""
        (mock_config_dir / "developer_token.json").write_text(
            json.dumps({"token": mock_developer_token, "expires": time.time() + 86400 * 60})
        )
        with patch.object(server, "can_generate_developer_token", return_value=True):
            out = server._config_auth_status()
        assert "generated" in out.lower() and "auto-renews" in out.lower()

    def test_expired_token(self, mock_config_dir, mock_developer_token):
        """Branch: days_left < 0 → EXPIRED."""
        (mock_config_dir / "developer_token.json").write_text(
            json.dumps({"token": mock_developer_token, "expires": time.time() - 86400})
        )
        with patch.object(server, "can_generate_developer_token", return_value=False):
            out = server._config_auth_status()
        assert "EXPIRED" in out

    def test_expires_in_7_days_or_fewer(self, mock_config_dir, mock_developer_token):
        """Branch: 0 < days_left <= 7 → EXPIRES IN X DAY(S) warning."""
        (mock_config_dir / "developer_token.json").write_text(
            json.dumps({"token": mock_developer_token, "expires": time.time() + 86400 * 3})
        )
        with patch.object(server, "can_generate_developer_token", return_value=False):
            out = server._config_auth_status()
        assert "EXPIRES IN" in out

    def test_error_reading_token(self, mock_config_dir):
        """Branch: dev_info is not None but reading it raises → ERROR reading token."""
        # Provide a token file so developer_token_info() returns something, then
        # arrange for the 'expires' key to be missing/broken
        (mock_config_dir / "developer_token.json").write_text(
            json.dumps({"token": "tok", "expires": "not-a-number"})
        )
        with patch.object(server, "can_generate_developer_token", return_value=False):
            out = server._config_auth_status()
        # "not-a-number" - time.time() will throw TypeError
        assert "ERROR reading token" in out

    def test_web_player_only_branch(self, mock_config_dir):
        """Branch: dev_info is None but has_any_developer_token() is True → web player."""
        with (
            patch.object(server, "developer_token_info", return_value=None),
            patch.object(server, "has_any_developer_token", return_value=True),
            patch.object(server, "has_user_token", return_value=False),
        ):
            out = server._config_auth_status()
        assert "web player" in out.lower()

    def test_api_connection_401(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Branch: API connection returns 401 → UNAUTHORIZED."""
        _write_tokens(mock_config_dir, mock_developer_token, mock_user_token)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with (
            patch.object(server, "get_headers", return_value={}),
            patch("requests.get", return_value=mock_resp),
        ):
            out = server._config_auth_status()
        assert "UNAUTHORIZED" in out

    def test_api_connection_429(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Branch: API connection returns 429 → RATE-LIMITED."""
        _write_tokens(mock_config_dir, mock_developer_token, mock_user_token)
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        with (
            patch.object(server, "get_headers", return_value={}),
            patch("requests.get", return_value=mock_resp),
        ):
            out = server._config_auth_status()
        assert "RATE-LIMITED" in out

    def test_api_connection_other_status(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Branch: API connection returns unexpected status."""
        _write_tokens(mock_config_dir, mock_developer_token, mock_user_token)
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with (
            patch.object(server, "get_headers", return_value={}),
            patch("requests.get", return_value=mock_resp),
        ):
            out = server._config_auth_status()
        assert "FAILED (503)" in out

    def test_api_connection_exception(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Branch: API request raises → API Connection: ERROR."""
        _write_tokens(mock_config_dir, mock_developer_token, mock_user_token)
        with (
            patch.object(server, "get_headers", return_value={}),
            patch("requests.get", side_effect=ConnectionError("timed out")),
        ):
            out = server._config_auth_status()
        assert "ERROR" in out and "timed out" in out


# ---------------------------------------------------------------------------
# Snapshot helper functions — called directly, not via library() dispatcher
# ---------------------------------------------------------------------------


class TestClearCredentials:
    def test_removes_present_key(self, mock_config_dir):
        """Key is present and cleared → appears in 'removed' list."""
        (mock_config_dir / "music_user_token.json").write_text('{"music_user_token":"tok"}')
        removed, failed = server._clear_credentials("music_user_token")
        assert "music_user_token" in removed
        assert not failed

    def test_key_not_present(self, mock_config_dir):
        """Key not present at all → neither removed nor failed."""
        removed, failed = server._clear_credentials("music_user_token")
        assert not removed
        assert not failed

    def test_cleared_false_goes_to_failed(self, mock_config_dir):
        """secret_delete returns False → key goes to failed, not removed."""
        (mock_config_dir / "music_user_token.json").write_text('{"music_user_token":"tok"}')
        with patch.object(server, "secret_delete", return_value=False):
            removed, failed = server._clear_credentials("music_user_token")
        assert "music_user_token" in failed
        assert not removed


# ---------------------------------------------------------------------------
# _auth_action — gap branches
# ---------------------------------------------------------------------------


class TestAuthActionGaps:
    def test_signin_exception_returns_error(self, monkeypatch):
        """signin_interactive raises → 'Error: ...'."""
        import applemusic_mcp.browser as browser

        monkeypatch.setattr(
            browser, "signin_interactive", lambda: (_ for _ in ()).throw(RuntimeError("no chrome"))
        )
        out = server._auth_action("signin")
        assert "Error" in out and "no chrome" in out

    def test_signin_generic_error_message(self, monkeypatch):
        """signin_interactive returns (False, non-waiting msg) → browser error text.
        Force the Chrome fallback: Safari harvest fails + Playwright present."""
        import applemusic_mcp.browser as browser

        monkeypatch.setattr(browser, "is_available", lambda: True)
        monkeypatch.setattr(browser, "signin_interactive", lambda: (False, "playwright not found"))
        out = server._auth_action("signin")
        assert "playwright not found" in out
        assert "Google Chrome installed" in out and "downloads itself" in out

    def test_logout_failed_credentials(self, mock_config_dir, monkeypatch):
        """_clear_credentials returns non-empty failed → ⚠️ Partly signed out."""
        import applemusic_mcp.browser as browser

        monkeypatch.setattr(browser, "clear_session", lambda: None)
        with patch.object(server, "_clear_credentials", return_value=([], ["music_user_token"])):
            out = server._auth_action("logout", confirm=True)
        assert "Partly signed out" in out
        assert "music_user_token" in out

    def test_reset_config_json_oserror(self, mock_config_dir, monkeypatch):
        """config.json exists but unlink raises OSError → goes to failed."""
        import applemusic_mcp.browser as browser

        monkeypatch.setattr(browser, "clear_session", lambda: None)
        (mock_config_dir / "config.json").write_text("{}")
        with (
            patch.object(server, "_clear_credentials", return_value=([], [])),
            patch("pathlib.Path.unlink", side_effect=OSError("locked")),
        ):
            out = server._auth_action("reset", confirm=True)
        # failed now contains "config.json" → ⚠️ Partial reset
        assert "Partial reset" in out

    def test_reset_partial_failed(self, mock_config_dir, monkeypatch):
        """_clear_credentials has failures → ⚠️ Partial reset."""
        import applemusic_mcp.browser as browser

        monkeypatch.setattr(browser, "clear_session", lambda: None)
        with patch.object(server, "_clear_credentials", return_value=([], ["developer_token"])):
            out = server._auth_action("reset", confirm=True)
        assert "Partial reset" in out

    def test_unknown_auth_action(self):
        out = server._auth_action("teleport")
        assert "Unknown action" in out
        assert "teleport" in out

    def test_login_alias(self, monkeypatch):
        """'login' is an alias for 'signin'."""
        import applemusic_mcp.browser as browser

        monkeypatch.setattr(browser, "signin_interactive", lambda: (True, "Signed in"))
        out = server._auth_action("login")
        assert "✓" in out

    def test_config_action_login_alias(self, monkeypatch):
        """config(action='login') routes to signin."""
        import applemusic_mcp.browser as browser

        monkeypatch.setattr(browser, "signin_interactive", lambda: (True, "Signed in"))
        out = server.config(action="login")
        assert "✓" in out

    def test_config_action_auth_status_alias(self, mock_config_dir):
        """config(action='auth_status') is also handled."""
        out = server.config(action="auth_status")
        # Should return auth status, not "Unknown action"
        assert "Developer Token" in out or "MISSING" in out


# ---------------------------------------------------------------------------
# Additional gap-filling tests for remaining uncovered lines
# ---------------------------------------------------------------------------


class TestClearExportsMbSize:
    """Line 6230: clear-exports total_size >= 1MB → MB suffix."""

    def test_reports_mb_size(self, tmp_path):
        f = tmp_path / "huge.csv"
        f.write_bytes(b"x" * (1024 * 1024 + 1))
        with patch.object(server, "get_cache_dir", return_value=tmp_path):
            out = server.config(action="clear-exports")
        assert "MB" in out


class TestConfigInfoCacheSizes:
    """Lines 6272, 6307, 6334-6337: size-format branches inside config info."""

    def _run_info(self, mock_config_dir, tmp_path, mock_cache, audit_log_path, snap_dir_path):
        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path),
            patch.object(al, "get_recent_entries", return_value=[]),
            patch.object(al, "get_audit_log_path", return_value=audit_log_path),
        ):
            return server.config(action="info")

    def test_cache_file_kb_size(self, mock_config_dir, tmp_path):
        """Line 6272: cache file >= 1KB but < 1MB → KB suffix."""
        mock_cache = MagicMock()
        mock_cache._cache = {"x": 1}
        cache_file = tmp_path / "track_cache.json"
        cache_file.write_bytes(b"x" * 1025)  # between 1KB and 1MB
        mock_cache.cache_file = cache_file
        snap_dir = tmp_path / "snaps"
        snap_dir.mkdir()

        out = self._run_info(
            mock_config_dir, tmp_path / "empty", mock_cache, tmp_path / "nope.log", snap_dir
        )
        assert "KB" in out

    def test_export_file_bytes_in_listing(self, mock_config_dir, tmp_path):
        """Line 6307: individual export file < 1KB → B suffix in per-file listing."""
        mock_cache = MagicMock()
        mock_cache._cache = {}
        mock_cache.cache_file = tmp_path / "tc.json"

        # Small file (< 1024 bytes) so it hits the `B` branch
        (tmp_path / "tiny.csv").write_bytes(b"a" * 100)
        snap_dir = tmp_path / "snaps"
        snap_dir.mkdir()

        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path),
            patch.object(al, "get_recent_entries", return_value=[]),
            patch.object(al, "get_audit_log_path", return_value=tmp_path / "nope.log"),
        ):
            out = server.config(action="info")
        assert "tiny.csv" in out
        assert "100B" in out or "B)" in out

    def test_export_file_mb_in_listing(self, mock_config_dir, tmp_path):
        """Line 6311: individual export file >= 1MB → MB suffix in per-file listing."""
        mock_cache = MagicMock()
        mock_cache._cache = {}
        mock_cache.cache_file = tmp_path / "tc.json"

        (tmp_path / "big.csv").write_bytes(b"x" * (1024 * 1024 + 1))
        snap_dir = tmp_path / "snaps"
        snap_dir.mkdir()

        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path),
            patch.object(al, "get_recent_entries", return_value=[]),
            patch.object(al, "get_audit_log_path", return_value=tmp_path / "nope.log"),
        ):
            out = server.config(action="info")
        assert "MB" in out

    def test_audit_log_kb_size(self, mock_config_dir, tmp_path):
        """Line 6334-6335: audit log >= 1KB but < 1MB → KB suffix."""
        mock_cache = MagicMock()
        mock_cache._cache = {}
        mock_cache.cache_file = tmp_path / "tc.json"

        audit_log_path = tmp_path / "audit.log"
        audit_log_path.write_bytes(b"x" * 1025)  # 1KB+1
        snap_dir = tmp_path / "snaps"
        snap_dir.mkdir()

        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path / "empty"),
            patch.object(al, "get_recent_entries", return_value=[1]),
            patch.object(al, "get_audit_log_path", return_value=audit_log_path),
        ):
            out = server.config(action="info")
        # Audit log shows KB size
        assert "Audit Log" in out

    def test_audit_log_mb_size(self, mock_config_dir, tmp_path):
        """Line 6336-6337: audit log >= 1MB → MB suffix."""
        mock_cache = MagicMock()
        mock_cache._cache = {}
        mock_cache.cache_file = tmp_path / "tc.json"

        audit_log_path = tmp_path / "audit.log"
        audit_log_path.write_bytes(b"x" * (1024 * 1024 + 1))
        snap_dir = tmp_path / "snaps"
        snap_dir.mkdir()

        from applemusic_mcp import audit_log as al

        with (
            patch.object(server, "get_track_cache", return_value=mock_cache),
            patch.object(server, "get_cache_dir", return_value=tmp_path / "empty"),
            patch.object(al, "get_recent_entries", return_value=[1]),
            patch.object(al, "get_audit_log_path", return_value=audit_log_path),
        ):
            out = server.config(action="info")
        assert "MB" in out


class TestAuthStatusMissingBranch:
    """Line 6416: Developer Token MISSING when no token at all and harvest disabled."""

    def test_completely_missing_dev_token(self, mock_config_dir):
        """Both developer_token_info=None and has_any_developer_token=False → MISSING."""
        with (
            patch.object(server, "developer_token_info", return_value=None),
            patch.object(server, "has_any_developer_token", return_value=False),
            patch.object(server, "has_user_token", return_value=False),
        ):
            out = server._config_auth_status()
        assert "MISSING" in out
        assert "Developer Token" in out


def _info(tmp_path):
    mock_cache = MagicMock()
    mock_cache._cache = {}
    mock_cache.cache_file = tmp_path / "track_cache.json"
    from applemusic_mcp import audit_log as al

    with (
        patch.object(server, "get_track_cache", return_value=mock_cache),
        patch.object(server, "get_cache_dir", return_value=tmp_path),
        patch.object(al, "get_recent_entries", return_value=[]),
        patch.object(al, "get_audit_log_path", return_value=tmp_path / "audit.log"),
    ):
        return server.config(action="info")


class TestConfigLevel:
    @pytest.mark.parametrize("mode", ["chrome", "web", "safari"])
    def test_set_pref_mode_refuses_above_level(self, monkeypatch, mock_config_dir, mode):
        monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
        from applemusic_mcp import auth

        monkeypatch.setattr(auth, "load_config", lambda: pytest.fail("preference was written"))
        out = server.config(action="set-pref", preference="mode", string_value=mode)
        assert "--level web" in out

    def test_set_pref_mode_within_level_saves(self, monkeypatch, mock_config_dir):
        monkeypatch.setenv("APPLEMUSIC_LEVEL", "web")
        out = server.config(action="set-pref", preference="mode", string_value="chrome")
        assert "Updated: mode = chrome" in out

    def test_info_shows_level(self, monkeypatch, mock_config_dir, tmp_path):
        monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
        assert "Permission level: api" in _info(tmp_path)

    def test_info_unrestricted_hint(self, mock_config_dir, tmp_path):
        assert "Permission level: unrestricted" in _info(tmp_path)
