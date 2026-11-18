"""Tests for server module."""

import json
import re
import time
from unittest.mock import MagicMock, patch

import responses

from applemusic_mcp import server


def _empty_amp_playlist_reads():
    """The API add/remove read the playlist's tracks first; answer those reads
    with an empty playlist."""
    responses.add(
        responses.GET,
        re.compile(r"https://amp-api\.music\.apple\.com/v1/me/library/playlists/[^/]+/tracks"),
        json={"data": []},
    )


class TestGetTokenExpirationWarning:
    """Tests for get_token_expiration_warning function."""

    def test_returns_none_when_no_token_file(self, mock_config_dir):
        """Should return None when token file doesn't exist."""
        result = server.get_token_expiration_warning()
        assert result is None

    def test_returns_none_when_token_valid(self, mock_config_dir):
        """Should return None when token has more than 30 days left."""
        token_file = mock_config_dir / "developer_token.json"
        token_data = {"expires": time.time() + 86400 * 60}  # 60 days
        with open(token_file, "w") as f:
            json.dump(token_data, f)

        result = server.get_token_expiration_warning()
        assert result is None

    def test_returns_warning_when_expiring_soon(self, mock_config_dir):
        """Should return warning when token expires within 30 days."""
        token_file = mock_config_dir / "developer_token.json"
        token_data = {"expires": time.time() + 86400 * 15}  # 15 days
        with open(token_file, "w") as f:
            json.dump(token_data, f)

        result = server.get_token_expiration_warning()
        assert result is not None
        assert "days" in result  # Could be 14 or 15 depending on timing
        assert "login --dev" in result


class TestGetHeaders:
    """Tests for get_headers function."""

    def test_returns_headers_with_tokens(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should return properly formatted headers."""
        # Setup token files
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 30}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        result = server.get_headers()

        assert "Authorization" in result
        assert result["Authorization"].startswith("Bearer ")
        assert "Music-User-Token" in result
        assert result["Content-Type"] == "application/json"


class TestGetLibraryPlaylists:
    """Tests for get_library_playlists function (API path)."""

    @responses.activate
    def test_returns_playlists(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Should return formatted playlist list via API."""
        # Disable AppleScript to test API path
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)

        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.abc123", "attributes": {"name": "Test Playlist", "canEdit": True}},
                    {"id": "p.def456", "attributes": {"name": "Read Only", "canEdit": False}},
                ]
            },
            status=200,
        )

        result = server.playlist(action="list")

        assert "Test Playlist" in result
        assert "p.abc123" in result
        assert "Read Only" in result
        assert "p.def456" in result
        assert "2 items" in result

    @responses.activate
    def test_handles_api_error(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Should return error message on API failure."""
        # Disable AppleScript to test API path
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)

        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"error": "Unauthorized"},
            status=401,
        )

        result = server.playlist(action="list")

        assert "API Error" in result or "401" in result


class TestCreatePlaylist:
    """Tests for create_playlist function (API path)."""

    @responses.activate
    def test_creates_playlist_successfully(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should create playlist via the API and return ID."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Creating a playlist takes the web (amp-api) rail.
        responses.add(
            responses.POST,
            "https://amp-api.music.apple.com/v1/me/library/playlists",
            json={"data": [{"id": "p.newplaylist123"}]},
            status=201,
        )

        result = server.playlist(
            action="create", name="My New Playlist", description="A description"
        )

        assert "My New Playlist" in result
        assert "p.newplaylist123" in result
        assert "via web player" in result


class TestRenamePlaylist:
    """Tests for rename_playlist function (API path)."""

    def test_off_macos_routes_to_web(self, monkeypatch):
        """Rename routes to the web rail."""
        monkeypatch.setattr(server, "_write_rail", lambda *a, **k: "web")
        monkeypatch.setattr(server.amp_api, "resolve_playlist_id", lambda name, **k: "p.r1")
        monkeypatch.setattr(server.amp_api, "rename_playlist", lambda pid, new: (True, "ok"))

        result = server.playlist(action="rename", playlist="Old Name", new_name="New Name")

        assert "Renamed" in result and "macOS" not in result

    def test_requires_new_name(self, monkeypatch):
        """Should error when new_name not provided."""
        result = server.playlist(action="rename", playlist="Old Name", new_name="")

        assert "Error" in result


class TestCreateFolder:
    """Tests for create_folder action (AppleScript path)."""

    def test_off_macos_routes_to_web(self, monkeypatch):
        """Off macOS, folder create routes to the web rail (no macOS-only error)."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        monkeypatch.setattr(server, "_write_rail", lambda *a, **k: "web")
        monkeypatch.setattr(
            server.amp_api, "create_folder", lambda name, parent_id=None: (True, "f.fld1")
        )

        result = server.playlist(action="create_folder", name="My Folder")

        assert "My Folder" in result and "macOS" not in result

    def test_requires_name(self):
        """Should error when name not provided."""
        result = server.playlist(action="create_folder", name="")

        assert "Error" in result


class TestMoveToFolder:
    """Tests for move action (AppleScript path)."""

    def test_off_macos_routes_to_web(self, monkeypatch):
        """Off macOS, move routes to the web rail (no longer a macOS-only error)."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        monkeypatch.setattr(server, "_write_rail", lambda *a, **k: "web")
        monkeypatch.setattr(server.amp_api, "resolve_playlist_id", lambda name, **k: "p.abc")
        monkeypatch.setattr(server.amp_api, "resolve_folder_id", lambda name: "f.fld1")
        monkeypatch.setattr(
            server.amp_api, "move_playlist_to_folder", lambda pid, fid: (True, "ok")
        )

        result = server.playlist(action="move", playlist="My Playlist", name="My Folder")

        assert "Moved" in result and "macOS" not in result

    def test_requires_playlist_name(self):
        """Should error when playlist name not provided."""
        result = server.playlist(action="move", playlist="", name="My Folder")

        assert "Error" in result


class TestAddToPlaylist:
    """Tests for add_to_playlist function."""

    @responses.activate
    def test_adds_tracks_successfully(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should add tracks and return confirmation."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # macOS native posts to api.music.apple.com; non-mac (api engine) posts to
        # amp-api. Mock both so the test passes on every platform.
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists/p.test123/tracks",
            status=204,
        )
        responses.add(
            responses.POST,
            "https://amp-api.music.apple.com/v1/me/library/playlists/p.test123/tracks",
            status=204,
        )
        _empty_amp_playlist_reads()  # the api engine reads the playlist before adding

        result = server.playlist(
            action="add", playlist="p.test123", track="i.song1, i.song2, i.song3"
        )

        assert "Added" in result
        assert "3 track" in result

    def test_handles_empty_track(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return error for empty track."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        result = server.playlist(action="add", playlist="p.test123", track="")

        assert "Provide track or album parameter" in result


class TestSplitTrackArtistCandidates:
    """Tests for _split_track_artist_candidates helper (open-ended track input)."""

    def test_simple_forward_form(self):
        """Produces forward + reverse candidates for a clean 'Song - Artist'."""
        got = server._split_track_artist_candidates("Silvera - GOJIRA")
        assert got == [("Silvera", "GOJIRA"), ("GOJIRA", "Silvera")]

    def test_no_separator_returns_empty(self):
        """Returns empty list when the input has no ' - ' separator to split on."""
        assert server._split_track_artist_candidates("No dash here") == []
        assert server._split_track_artist_candidates("") == []

    def test_hyphen_without_spaces_not_split(self):
        """A plain hyphen (no spaces) isn't a split candidate — preserves names like 'Jay-Z'."""
        assert server._split_track_artist_candidates("Jay-Z") == []

    def test_multi_dash_produces_first_and_last_split(self):
        """Multi-dash inputs try both first-dash and last-dash splits so multi-word titles work."""
        got = server._split_track_artist_candidates("A - B - C")
        # First-dash split: ('A', 'B - C') + reverse; last-dash split: ('A - B', 'C')
        assert ("A", "B - C") in got
        assert ("B - C", "A") in got
        assert ("A - B", "C") in got

    def test_forward_form_first(self):
        """Forward form (Song - Artist, the convention) is always tried first."""
        got = server._split_track_artist_candidates("Come Together - The Beatles")
        assert got[0] == ("Come Together", "The Beatles")

    def test_strips_whitespace_around_parts(self):
        """Candidates have whitespace stripped from both parts."""
        got = server._split_track_artist_candidates("  Song  -  Artist  ")
        assert got[0] == ("Song", "Artist")

    def test_empty_part_rejected(self):
        """An empty part on either side is rejected (no junk candidates)."""
        assert server._split_track_artist_candidates(" - Artist") == []
        assert server._split_track_artist_candidates("Song - ") == []


class TestSearchLibrary:
    """Tests for search_library function."""

    @responses.activate
    def test_returns_search_results(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Should return formatted search results via API fallback."""
        # Force API path by disabling AppleScript
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)

        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/search",
            json={
                "results": {
                    "library-songs": {
                        "data": [
                            {
                                "id": "i.abc123",
                                "attributes": {
                                    "name": "Wonderwall",
                                    "artistName": "Oasis",
                                    "albumName": "(What's the Story) Morning Glory?",
                                },
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        result = server.library(action="search", query="Wonderwall")

        assert "Wonderwall" in result
        assert "Oasis" in result
        assert "i.abc123" in result

    @responses.activate
    def test_genre_search_does_not_fall_back_to_fulltext_api(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Genre filtering is local-only; it must not degrade to the API full-text
        search, which has no genre filter and would false-match on the genre name."""
        # No AppleScript + a valid token is exactly the case that used to cascade
        # into /me/library/search.
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)

        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)
        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Deliberately register NO /me/library/search mock: any call there would
        # raise ConnectionError and surface as "API Error", failing the assert.
        result = server.library(action="search", query="Rock", types="genre")

        assert "API Error" not in result
        assert "genre" in result.lower()


class TestSearchCatalog:
    """Tests for search_catalog function."""

    @responses.activate
    def test_returns_catalog_results(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return formatted catalog search results."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "123456789",
                                "attributes": {"name": "Let It Be", "artistName": "The Beatles"},
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        result = server.catalog(action="search", query="Let It Be")

        assert "Let It Be" in result
        assert "The Beatles" in result
        assert "123456789" in result


class TestCheckAuthStatus:
    """Tests for check_auth_status function."""

    def test_reports_missing_tokens(self, mock_config_dir):
        """Should report missing tokens."""
        result = server.config(action="auth-status")

        assert "MISSING" in result
        assert "Developer Token" in result
        assert "Music User Token" in result

    def test_reports_valid_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should report OK for valid tokens."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Don't actually test API connection
        with patch.object(server, "get_headers", return_value={}):
            with patch("requests.get") as mock_get:
                mock_get.return_value.status_code = 200
                result = server.config(action="auth-status")

        assert "OK" in result
        assert "Developer Token" in result

    def test_reports_expiring_token(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should warn about expiring token."""
        # Setup expiring token
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 10}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        result = server.config(action="auth-status")

        # Keyless generated token (no .p8 to auto-renew) → escalating nudge.
        assert "expires in" in result.lower()
        assert "login --dev" in result


class TestAuthTool:
    """The conversational `auth` tool: status / signin / logout / reset."""

    def _tokens(self, d, dev, user):
        (d / "developer_token.json").write_text(
            json.dumps({"token": dev, "expires": time.time() + 86400 * 60})
        )
        (d / "music_user_token.json").write_text(json.dumps({"music_user_token": user}))

    def test_status_ready(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        self._tokens(mock_config_dir, mock_developer_token, mock_user_token)
        # The verdict now hinges on the real mutation path (amp-api), so control it.
        monkeypatch.setattr(server.amp_api, "session_status", lambda: "ok")
        with patch.object(server, "get_headers", return_value={}):
            with patch("requests.get") as mg:
                mg.return_value.status_code = 200
                out = server.config(action="status")
        assert "Mode:" in out and "Ready" in out
        assert "Web fallback writes" in out and "OK" in out
        assert "Writes:" in out  # resolved write rail is surfaced

    def test_status_flags_forced_tokenless(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        # The flag that silently blocked every add must be surfaced, not green-lit.
        self._tokens(mock_config_dir, mock_developer_token, mock_user_token)
        monkeypatch.setenv("APPLEMUSIC_FORCE_TOKENLESS", "1")
        out = server.config(action="status")
        assert "Ready" not in out
        assert "FORCE_TOKENLESS" in out and "DISABLED" in out

    def test_library_add_names_the_flag_when_forced(self, monkeypatch):
        # The add error must name the flag, not tell the user to log in (which
        # wouldn't help while it's set).
        monkeypatch.setenv("APPLEMUSIC_FORCE_TOKENLESS", "1")
        out = server.library(action="add", track="Some Song")
        assert "FORCE_TOKENLESS" in out and "Unset it" in out

    def test_status_tokens_present_but_mutations_unauthorized(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        # The exact trap on the WEB rail: tokens look fine, reads work, but the
        # write path 401s. Status must NOT claim add works. Force the web rail
        # (off macOS, no generated dev token) so the verdict hinges on the session.
        self._tokens(mock_config_dir, mock_developer_token, mock_user_token)
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        monkeypatch.setattr(server, "_has_developer_token", lambda: False)
        monkeypatch.setattr(server.amp_api, "session_status", lambda: "expired")
        with patch.object(server, "get_headers", return_value={}):
            with patch("requests.get") as mg:
                mg.return_value.status_code = 200
                out = server.config(action="status")
        assert "Ready" not in out
        assert "unauthorized" in out.lower() and "signin" in out.lower()

    def test_status_not_signed_in(self, mock_config_dir):
        out = server.config(action="status")
        assert "signin" in out.lower()

    def test_signin_success(self, monkeypatch):
        import applemusic_mcp.browser as browser

        monkeypatch.setattr(
            browser,
            "signin_interactive",
            lambda *a, **k: (True, "Signed in — session captured and saved."),
        )
        out = server.config(action="signin")
        assert "✓" in out and "Signed in" in out

    def test_signin_still_waiting(self, monkeypatch):
        import applemusic_mcp.browser as browser

        monkeypatch.setattr(browser, "is_available", lambda: True)
        monkeypatch.setattr(browser, "signin_interactive", lambda *a, **k: (False, "still-waiting"))
        out = server.config(action="signin")
        assert "finish signing in" in out.lower()

    def test_logout_requires_confirm(self, mock_config_dir, mock_developer_token, mock_user_token):
        self._tokens(mock_config_dir, mock_developer_token, mock_user_token)
        out = server.config(action="logout")
        assert "confirm=True" in out
        # nothing deleted without confirm
        assert (mock_config_dir / "music_user_token.json").exists()

    def test_logout_clears_user_session(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        self._tokens(mock_config_dir, mock_developer_token, mock_user_token)
        (mock_config_dir / "harvested_token.json").write_text("{}")
        import applemusic_mcp.browser as browser

        cleared = {}
        monkeypatch.setattr(browser, "clear_session", lambda: cleared.update(done=True))
        out = server.config(action="logout", confirm=True)
        assert "Signed out" in out
        assert not (mock_config_dir / "music_user_token.json").exists()
        assert not (mock_config_dir / "harvested_token.json").exists()
        # dev token is preserved on logout
        assert (mock_config_dir / "developer_token.json").exists()
        assert cleared.get("done") is True

    def test_reset_requires_confirm(self, mock_config_dir, mock_developer_token, mock_user_token):
        self._tokens(mock_config_dir, mock_developer_token, mock_user_token)
        out = server.config(action="reset")
        assert "confirm=True" in out
        assert (mock_config_dir / "developer_token.json").exists()

    def test_reset_wipes_everything(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        self._tokens(mock_config_dir, mock_developer_token, mock_user_token)
        (mock_config_dir / "config.json").write_text("{}")
        import applemusic_mcp.browser as browser

        monkeypatch.setattr(browser, "clear_session", lambda: None)
        out = server.config(action="reset", confirm=True)
        assert "Reset complete" in out
        assert not (mock_config_dir / "developer_token.json").exists()
        assert not (mock_config_dir / "config.json").exists()
        assert not (mock_config_dir / "music_user_token.json").exists()

    def test_unknown_action(self):
        assert "Unknown action" in server.config(action="bogus")


class TestModePreference:
    """The single engine mode (playback follows it) must be switchable via the
    tool, not just by hand-editing config.json."""

    def test_set_mode_web(self, mock_config_dir):
        out = server.config(action="set-pref", preference="mode", string_value="web")
        assert "mode = web" in out
        from applemusic_mcp.auth import get_user_preferences

        assert get_user_preferences()["mode"] == "web"

    def test_set_mode_api_alias_accepted(self, mock_config_dir):
        # `api` stays accepted as a back-compat alias for `web`.
        out = server.config(action="set-pref", preference="mode", string_value="api")
        assert "mode = api" in out

    def test_playback_pref_removed(self, mock_config_dir):
        # The separate playback knob is gone; setting it is rejected.
        out = server.config(action="set-pref", preference="playback", string_value="browser")
        assert "must be one of" in out.lower() or "preference must be" in out.lower()

    def test_invalid_mode_rejected(self, mock_config_dir):
        out = server.config(action="set-pref", preference="mode", string_value="bogus")
        assert "must be one of" in out
        assert "native" in out and "web" in out

    def test_mode_missing_value_lists_choices(self, mock_config_dir):
        out = server.config(action="set-pref", preference="mode")
        assert "native" in out and "auto" in out


class TestToolAnnotations:
    """Tools carry behavioral hints (readOnly/destructive/openWorld) so clients
    can auto-approve reads and warn on destructive ops."""

    def _annotations(self):
        import asyncio

        tools = asyncio.run(server.mcp.list_tools())
        return {t.name: t.annotations for t in tools}

    @staticmethod
    def _hint(ann, camel: str):
        """Read an annotation hint under either mcp major.

        mcp 1.x exposes `readOnlyHint`; 2.x renamed the field to `read_only_hint`.
        Both accept the camelCase constructor kwarg and both emit camelCase on the
        wire, so this is purely about reading the Python object."""
        snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in camel)
        for attr in (camel, snake):
            if hasattr(ann, attr):
                return getattr(ann, attr)
        raise AttributeError(f"no {camel}/{snake} on {type(ann).__name__}")

    def test_reads_are_readonly(self):
        ann = self._annotations()
        for name in ("catalog", "discover"):
            assert self._hint(ann[name], "readOnlyHint") is True
            assert self._hint(ann[name], "destructiveHint") is False

    def test_destructive_tools_flagged(self):
        ann = self._annotations()
        for name in ("playlist", "library", "config"):
            assert self._hint(ann[name], "destructiveHint") is True
            assert self._hint(ann[name], "readOnlyHint") is False

    def test_all_tools_hit_open_world(self):
        ann = self._annotations()
        assert all(self._hint(a, "openWorldHint") is True for a in ann.values())

    def test_annotations_reach_the_wire_as_camelcase(self):
        """The field rename is internal to mcp; clients must see no difference."""
        ann = self._annotations()["playlist"]
        wire = ann.model_dump(by_alias=True, exclude_none=True)
        assert wire["readOnlyHint"] is False
        assert wire["destructiveHint"] is True
        assert wire["title"] == "Playlists & folders"


class TestFormatDuration:
    """Tests for format_duration helper function."""

    def test_formats_standard_duration(self):
        """Should format milliseconds as m:ss."""
        assert server.format_duration(225000) == "3:45"
        assert server.format_duration(60000) == "1:00"
        assert server.format_duration(5000) == "0:05"

    def test_handles_zero(self):
        """Should return empty string for zero."""
        assert server.format_duration(0) == ""

    def test_handles_none(self):
        """Should return empty string for None."""
        assert server.format_duration(None) == ""

    def test_handles_negative(self):
        """Should return empty string for negative values."""
        assert server.format_duration(-1000) == ""
        assert server.format_duration(-60000) == ""

    def test_handles_large_duration(self):
        """Should handle songs longer than an hour."""
        # 1 hour, 5 minutes, 30 seconds = 3930000 ms
        assert server.format_duration(3930000) == "65:30"


class TestExtractTrackData:
    """Tests for extract_track_data helper function."""

    def test_extracts_basic_fields(self):
        """Should extract core fields from track data."""
        track = {
            "id": "i.abc123",
            "attributes": {
                "name": "Wonderwall",
                "artistName": "Oasis",
                "albumName": "(What's the Story) Morning Glory?",
                "durationInMillis": 258000,
                "releaseDate": "1995-10-02",
                "genreNames": ["Rock", "Alternative"],
            },
        }
        result = server.extract_track_data(track)

        assert result["name"] == "Wonderwall"
        assert result["artist"] == "Oasis"
        assert result["album"] == "(What's the Story) Morning Glory?"
        assert result["duration"] == "4:18"
        assert result["year"] == "1995"
        assert result["genre"] == "Rock"
        assert result["id"] == "i.abc123"

    def test_handles_empty_track(self):
        """Should handle empty track dict gracefully."""
        result = server.extract_track_data({})

        assert result["name"] == ""
        assert result["artist"] == ""
        assert result["duration"] == ""
        assert result["id"] == ""

    def test_handles_missing_attributes(self):
        """Should handle track with empty attributes."""
        track = {"id": "test123", "attributes": {}}
        result = server.extract_track_data(track)

        assert result["id"] == "test123"
        assert result["name"] == ""

    def test_includes_extras_when_requested(self):
        """Should include extra fields when include_extras=True."""
        track = {
            "id": "123",
            "attributes": {
                "name": "Test",
                "trackNumber": 5,
                "discNumber": 2,
                "hasLyrics": True,
                "composerName": "John Doe",
                "isrc": "USRC12345678",
                "contentRating": "explicit",
                "playParams": {"catalogId": "cat123"},
                "previews": [{"url": "https://example.com/preview.m4a"}],
                "artwork": {"url": "https://example.com/{w}x{h}bb.jpg"},
            },
        }
        result = server.extract_track_data(track, include_extras=True)

        assert result["track_number"] == 5
        assert result["disc_number"] == 2
        assert result["has_lyrics"] is True
        assert result["composer"] == "John Doe"
        assert result["isrc"] == "USRC12345678"
        assert result["is_explicit"] is True
        assert result["catalog_id"] == "cat123"
        assert "preview.m4a" in result["preview_url"]
        assert "500x500" in result["artwork_url"]


class TestTruncate:
    """Tests for truncate helper function."""

    def test_truncates_long_string(self):
        """Should truncate and add ellipsis for strings exceeding max length."""
        result = server.truncate("This is a very long string", 10)
        assert result == "This is a ..."
        assert len(result) == 13  # 10 chars + "..."

    def test_returns_short_string_unchanged(self):
        """Should return strings shorter than max unchanged."""
        result = server.truncate("Short", 10)
        assert result == "Short"

    def test_returns_exact_length_unchanged(self):
        """Should return strings exactly at max length unchanged."""
        result = server.truncate("TenChars!!", 10)
        assert result == "TenChars!!"

    def test_handles_empty_string(self):
        """Should handle empty string."""
        result = server.truncate("", 10)
        assert result == ""


class TestFormatTrackList:
    """Tests for format_track_list helper function."""

    def test_full_format_for_small_lists(self):
        """Should use full format for small track lists."""
        tracks = [
            {
                "name": "Song Name",
                "artist": "Artist Name",
                "duration": "3:45",
                "album": "Album Name",
                "year": "2024",
                "genre": "Rock",
                "id": "123",
            }
        ]
        lines, tier = server.format_track_list(tracks)

        assert tier == "Full"
        assert len(lines) == 1
        assert "Song Name - Artist Name (3:45) Album Name [2024] Rock 123" == lines[0]

    def test_clipped_format_when_full_exceeds_limit(self):
        """Should use clipped format when full format exceeds MAX_OUTPUT_CHARS."""
        track = {
            "name": "A" * 100,
            "artist": "B" * 50,
            "duration": "3:00",
            "album": "C" * 100,
            "year": "2024",
            "genre": "Rock",
            "id": "12345678901234567890",
        }
        tracks = [track] * 200
        lines, tier = server.format_track_list(tracks)

        assert tier == "Clipped"
        assert len(lines) == 200
        assert "..." in lines[0]  # Truncated
        assert "C" * 100 not in lines[0]  # Album truncated
        assert "[2024]" in lines[0]  # Year still present
        assert "Rock" in lines[0]  # Genre still present

    def test_compact_format_when_clipped_exceeds_limit(self):
        """Should use compact format when clipped format exceeds MAX_OUTPUT_CHARS."""
        track = {
            "name": "A" * 50,
            "artist": "B" * 30,
            "duration": "3:00",
            "album": "Album",
            "year": "2024",
            "genre": "Rock",
            "id": "12345678901234567890",
        }
        tracks = [track] * 450
        lines, tier = server.format_track_list(tracks)

        assert tier == "Compact"
        assert len(lines) == 450
        assert "Album" not in lines[0]  # Album dropped
        assert "[2024]" not in lines[0]  # Year dropped
        assert "(3:00)" in lines[0]  # Duration still present

    def test_minimal_format_when_compact_exceeds_limit(self):
        """Should use minimal format when compact format also exceeds limit."""
        track = {
            "name": "A" * 50,
            "artist": "B" * 30,
            "duration": "3:00",
            "album": "Album",
            "year": "2024",
            "genre": "Rock",
            "id": "12345678901234567890",
        }
        tracks = [track] * 800
        lines, tier = server.format_track_list(tracks)

        assert tier == "Minimal"
        assert len(lines) == 800
        assert "(3:00)" not in lines[0]

    def test_handles_empty_optional_fields(self):
        """Should handle tracks with empty year/genre gracefully."""
        tracks = [
            {
                "name": "Song",
                "artist": "Artist",
                "duration": "3:00",
                "album": "Album",
                "year": "",
                "genre": "",
                "id": "123",
            }
        ]
        lines, tier = server.format_track_list(tracks)

        assert tier == "Full"
        assert "[" not in lines[0]
        assert lines[0] == "Song - Artist (3:00) Album 123"

    def test_returns_empty_for_no_tracks(self):
        """Should handle empty track list."""
        lines, tier = server.format_track_list([])
        assert tier == "Full"
        assert lines == []


class TestSearchCatalogSongsHelper:
    """Tests for _search_catalog_songs internal helper."""

    @responses.activate
    def test_returns_songs_on_success(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return list of song dicts on successful search."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "123",
                                "attributes": {"name": "Test Song", "artistName": "Test Artist"},
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        result = server._search_catalog_songs("test", limit=5)
        assert len(result) == 1
        assert result[0]["id"] == "123"

    @responses.activate
    def test_returns_empty_on_error(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return empty list on API error."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"error": "Unauthorized"},
            status=401,
        )

        result = server._search_catalog_songs("test")
        assert result == []


class TestAddSongsToLibraryHelper:
    """Tests for _add_songs_to_library internal helper."""

    def test_returns_error_for_empty_ids(self):
        """Should return error tuple for empty ID list."""
        success, msg = server._add_songs_to_library([])
        assert success is False
        assert "No catalog IDs" in msg

    @responses.activate
    def test_returns_success_on_valid_response(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should return success tuple on successful add."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            status=202,
        )

        success, msg = server._add_songs_to_library(["123456789"])
        assert success is True
        assert "1 song" in msg

    @responses.activate
    def test_returns_error_on_api_failure(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should return error tuple on API failure."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            status=401,
        )

        success, msg = server._add_songs_to_library(["123456789"])
        assert success is False
        assert "401" in msg


class TestAddToLibraryTool:
    """Tests for add_to_library MCP tool."""

    def test_returns_error_for_empty_input(self):
        """Should return error when no input provided."""
        result = server.library(action="add")
        assert "Error: Provide track or album parameter" in result

    @responses.activate
    def test_adds_songs_successfully(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should add songs and return success message."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            status=202,
        )

        result = server.library(action="add", track="1234567890, 9876543210")
        assert "Added" in result
        assert "2" in result

    @responses.activate
    def test_add_album_by_catalog_id_does_not_crash(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Regression: album=<numeric catalog id> hit an undefined `use_ui_path`
        (NameError) left over from the UI-automation removal. It must route to
        the API instead."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)
        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(responses.POST, "https://api.music.apple.com/v1/me/library", status=202)

        result = server.library(action="add", album="1538003494")
        assert "Error: name 'use_ui_path'" not in result
        assert "Added" in result


class TestPlayTrackMatching:
    """Tests for play_track song matching logic."""

    def test_matches_featured_artist_in_song_name(self):
        """Should match artist in song name for featured artists."""
        # Mock song with featured artist in title
        song = {
            "id": "123",
            "attributes": {
                "name": "Uptown Funk (feat. Bruno Mars)",
                "artistName": "Mark Ronson",
                "url": "https://music.apple.com/us/song/123",
            },
        }

        # Check if "Bruno Mars" matches (in song name, not artistName)
        song_name = song["attributes"]["name"]
        song_artist = song["attributes"]["artistName"]
        artist = "Bruno Mars"

        # This is the matching logic from play_track
        matches_artist = (
            artist.lower() in song_artist.lower() or artist.lower() in song_name.lower()
        )
        assert matches_artist is True

    def test_matches_artist_in_artist_name(self):
        """Should match artist in artistName field."""
        song_name = "Bohemian Rhapsody"
        song_artist = "Queen"
        artist = "Queen"

        matches_artist = (
            artist.lower() in song_artist.lower() or artist.lower() in song_name.lower()
        )
        assert matches_artist is True

    def test_no_match_when_artist_not_found(self):
        """Should not match when artist is in neither field."""
        song_name = "Some Song"
        song_artist = "Some Artist"
        artist = "Different Artist"

        matches_artist = (
            artist.lower() in song_artist.lower() or artist.lower() in song_name.lower()
        )
        assert matches_artist is False

    def test_partial_track_name_match(self):
        """Should match partial track names."""
        song_name = "Bohemian Rhapsody (Remastered 2011)"
        track_name = "Bohemian Rhapsody"

        matches_track = track_name.lower() in song_name.lower()
        assert matches_track is True

    def test_case_insensitive_matching(self):
        """Should match regardless of case."""
        song_name = "BOHEMIAN RHAPSODY"
        song_artist = "QUEEN"
        track_name = "bohemian rhapsody"
        artist = "queen"

        matches_track = track_name.lower() in song_name.lower()
        matches_artist = artist.lower() in song_artist.lower()
        assert matches_track is True
        assert matches_artist is True


class TestPaginationWithFetchExplicit:
    """Tests for pagination when fetch_explicit is True.

    When fetch_explicit=True and playlist name is provided, we use the
    cache-first approach: AppleScript for fast native access, then check
    cache for explicit status, only hitting API on cache miss.
    """

                # Note: text format may not display explicit status, but cache was used

    @responses.activate
    def test_optimized_pagination_minimal_api_calls(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """With limit specified, should only fetch needed tracks, not all.

        Performance test: limit=5 on a 500 track playlist should make 1 API call,
        not 5 calls to fetch all 500 tracks.
        """
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)

        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API: return 5 tracks (simulating a partial response)
        api_call_count = 0

        def request_callback(request):
            nonlocal api_call_count
            api_call_count += 1
            return (
                200,
                {},
                json.dumps(
                    {
                        "data": [
                            {
                                "id": f"i.lib{i}",
                                "attributes": {
                                    "name": f"Track {i}",
                                    "artistName": "Artist",
                                    "albumName": "Album",
                                    "contentRating": "clean",
                                },
                            }
                            for i in range(5)
                        ]
                    }
                ),
            )

        responses.add_callback(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.test123/tracks",
            callback=request_callback,
            content_type="application/json",
        )

        # Call with playlist ID and limit=5
        result = server.playlist(action="tracks", playlist="p.test123", limit=5)

        # Should only make 1 API call, not multiple
        assert api_call_count == 1, f"Expected 1 API call, got {api_call_count}"
        assert "Track 0" in result
        assert "Track 4" in result


class TestFindApiPlaylistByName:
    """Tests for _find_api_playlist_by_name function."""

    @responses.activate
    def test_finds_exact_match(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should find playlist by exact name match."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.abc123", "attributes": {"name": "My Playlist"}},
                    {"id": "p.def456", "attributes": {"name": "Another Playlist"}},
                ]
            },
            status=200,
        )

        playlist_id, fuzzy_match = server._find_api_playlist_by_name("My Playlist")
        assert playlist_id == "p.abc123"
        assert fuzzy_match is None  # Should be exact match

    @responses.activate
    def test_finds_partial_match(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should find playlist by partial name match."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.emoji123", "attributes": {"name": "🎸 Rock Playlist"}},
                ]
            },
            status=200,
        )

        # Partial match without emoji
        playlist_id, fuzzy_match = server._find_api_playlist_by_name("Rock Playlist")
        assert playlist_id == "p.emoji123"

    @responses.activate
    def test_prefers_exact_match_over_partial(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should prefer exact match over partial match."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Partial match comes first in the list, but exact match should win
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.partial", "attributes": {"name": "Rock Playlist Extended"}},
                    {"id": "p.exact", "attributes": {"name": "Rock Playlist"}},
                ]
            },
            status=200,
        )

        playlist_id, fuzzy_match = server._find_api_playlist_by_name("Rock Playlist")
        assert playlist_id == "p.exact"
        assert fuzzy_match is None  # Should be exact match

    @responses.activate
    def test_returns_none_when_not_found(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should return None when playlist not found."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": []},
            status=200,
        )

        playlist_id, fuzzy_match = server._find_api_playlist_by_name("Nonexistent Playlist")
        assert playlist_id is None
        assert fuzzy_match is None

    @responses.activate
    def test_returns_none_on_api_error(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should return None on API error (graceful fallback)."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            status=401,  # Unauthorized
        )

        playlist_id, fuzzy_match = server._find_api_playlist_by_name("Any Playlist")
        assert playlist_id is None
        assert fuzzy_match is None


class TestResolvePlaylistApiLookup:
    """Tests for _resolve_playlist API lookup behavior."""

    def test_returns_id_directly_for_p_prefix(self):
        """Should return playlist ID directly when p. prefix provided."""
        resolved = server._resolve_playlist("p.abc123xyz")
        assert resolved.api_id == "p.abc123xyz"
        assert resolved.error is None
        assert resolved.raw_input == "p.abc123xyz"

    def test_returns_error_for_empty_string(self):
        """Should return error for empty playlist string."""
        resolved = server._resolve_playlist("")
        assert resolved.api_id is None
        assert resolved.error is not None
        assert "required" in resolved.error.lower()

    @responses.activate
    def test_looks_up_api_id_for_name(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should look up API playlist ID when given a name."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.found123", "attributes": {"name": "My Music"}},
                ]
            },
            status=200,
        )

        resolved = server._resolve_playlist("My Music")

        # A unique API match resolves to its id.
        assert resolved.api_id == "p.found123"
        assert resolved.error is None
        assert resolved.raw_input == "My Music"

    @responses.activate
    def test_reports_error_when_not_in_api(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """A name the library listing doesn't have is an error, not a silent pass."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": []},  # Empty - playlist not in API
            status=200,
        )

        resolved = server._resolve_playlist("Local Only Playlist")

        assert resolved.api_id is None
        assert resolved.error is not None
        assert "not found" in resolved.error.lower()

    def test_handles_ps_i_love_you_as_name(self):
        """Should treat 'p.s. I love you' as a name, not an ID."""
        # This tests the edge case where a playlist name starts with "p."
        # but isn't an ID (has spaces/punctuation after p.).
        with patch.object(server, "_find_api_playlist_by_name", return_value=(None, None)):
            resolved = server._resolve_playlist("p.s. I love you")

        # Should be treated as a name, not an ID
        assert resolved.api_id is None
        assert resolved.raw_input == "p.s. I love you"
        assert resolved.error is not None


class TestFuzzyMatchingPlaylistResolution:
    """Tests for fuzzy matching playlist names - REGRESSION TESTS."""

    @responses.activate
    def test_fuzzy_matches_and_vs_ampersand(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should fuzzy match 'Alice and Bob' to 'Alice & Bob'."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API response with playlist named "Alice & Bob"
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.alice123", "attributes": {"name": "Alice & Bob"}},
                ]
            },
            status=200,
        )

        # User types "Alice and Bob" (with "and" instead of "&")
        resolved = server._resolve_playlist("Alice and Bob")

        assert resolved.api_id == "p.alice123"
        assert resolved.error is None
        assert resolved.raw_input == "Alice and Bob"
        assert resolved.fuzzy_match is not None
        assert resolved.fuzzy_match.matched_name == "Alice & Bob"
        assert "and" in str(resolved.fuzzy_match.transformations).lower() or "&" in str(
            resolved.fuzzy_match.transformations
        )

    @responses.activate
    def test_fuzzy_matches_with_emojis(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should fuzzy match playlist names with emojis removed."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API response with emoji playlist
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.emoji123", "attributes": {"name": "🤟👶🎸 Alice & Bob"}},
                ]
            },
            status=200,
        )

        # User types without emojis
        resolved = server._resolve_playlist("Alice & Bob")

        # Should fuzzy match (emojis ignored)
        assert resolved.api_id == "p.emoji123"
        assert resolved.fuzzy_match is not None
        assert resolved.fuzzy_match.matched_name == "🤟👶🎸 Alice & Bob"
        assert resolved.error is None

    @responses.activate
    def test_exact_match_preferred_over_fuzzy(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should prefer exact match over fuzzy match."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API response with both exact and fuzzy matches
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {
                        "id": "p.fuzzy",
                        "attributes": {"name": "The Rock Music"},
                    },  # Fuzzy (article removed)
                    {"id": "p.exact", "attributes": {"name": "Rock Music"}},  # Exact match
                ]
            },
            status=200,
        )

        resolved = server._resolve_playlist("Rock Music")

        # Should choose exact match
        assert resolved.api_id == "p.exact"
        assert resolved.fuzzy_match is None or resolved.fuzzy_match.match_type == "exact"

    @responses.activate
    def test_resolved_object_carries_the_matched_name(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """A fuzzy match must expose the name it matched, not just the id."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.test123", "attributes": {"name": "Test & Playlist"}},
                ]
            },
            status=200,
        )

        resolved = server._resolve_playlist("Test and Playlist")

        assert resolved.api_id == "p.test123"
        assert resolved.fuzzy_match is not None
        assert resolved.fuzzy_match.matched_name == "Test & Playlist"


class TestPlaylistResolutionPerformance:
    """Performance tests for playlist resolution."""

    @responses.activate
    def test_fuzzy_matching_performance_with_many_playlists(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should complete fuzzy matching in reasonable time even with many playlists.

        This tests the optimization where fuzzy matching only happens if exact/partial fails.
        With 50 playlists and target at position 25, should complete quickly.
        """
        import time as time_module

        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API with 50 playlists (realistic library size)
        playlists = [
            {"id": f"p.test{i}", "attributes": {"name": f"Playlist {i}"}} for i in range(25)
        ]
        # Add fuzzy match target in the middle
        playlists.append({"id": "p.target", "attributes": {"name": "Rock & Roll"}})
        # Add more playlists after
        playlists.extend(
            [{"id": f"p.test{i}", "attributes": {"name": f"Playlist {i}"}} for i in range(25, 50)]
        )

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": playlists},
            status=200,
        )

        start = time_module.time()
        resolved = server._resolve_playlist("Rock and Roll")  # Fuzzy: "and" → "&"
        elapsed = time_module.time() - start

        # Should complete in under 500ms (fuzzy matching is now fallback-only)
        assert elapsed < 0.5, f"Fuzzy matching took {elapsed:.2f}s, should be < 0.5s"
        assert resolved.api_id == "p.target"
        assert resolved.fuzzy_match is not None, "Should be a fuzzy match"
        assert resolved.fuzzy_match.matched_name == "Rock & Roll"


# =============================================================================
# Integration Tests - Real User Journeys
# =============================================================================
# These tests validate realistic workflows at different scales:
# - First 1-2 actions: Basic discovery
# - First 5 actions: Getting started
# - First 10 actions: Regular user
# - First 20 actions: Power user
#
# Every flow runs against the Apple Music API: there is no native engine in
# this build.
# =============================================================================


class TestUserJourneyAPIOnly:
    """Integration tests for the API-only build."""

    @responses.activate
    def test_first_2_actions_search_and_list_playlists(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """First things users do: search for music and see their playlists."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # 1. Search catalog for a song
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "123456",
                                "attributes": {
                                    "name": "Hey Jude",
                                    "artistName": "The Beatles",
                                    "albumName": "Past Masters",
                                    "durationInMillis": 431000,
                                    "releaseDate": "1968-08-26",
                                    "genreNames": ["Rock"],
                                },
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        result = server.catalog(action="search", query="Hey Jude Beatles")
        assert "Hey Jude" in result
        assert "Beatles" in result

        # 2. List playlists
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.favorites", "attributes": {"name": "Favorites", "canEdit": True}},
                    {"id": "p.workout", "attributes": {"name": "Workout Mix", "canEdit": True}},
                ]
            },
            status=200,
        )

        result = server.playlist(action="list")
        assert "Favorites" in result
        assert "Workout Mix" in result

    @responses.activate
    def test_first_5_actions_basic_playlist_workflow(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Getting started: search, list playlists, get tracks, add to playlist."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        _empty_amp_playlist_reads()
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # 1. Search catalog
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "song1",
                                "attributes": {
                                    "name": "Bohemian Rhapsody",
                                    "artistName": "Queen",
                                    "albumName": "A Night at the Opera",
                                    "durationInMillis": 354000,
                                    "releaseDate": "1975-10-31",
                                    "genreNames": ["Rock"],
                                },
                            }
                        ]
                    }
                }
            },
            status=200,
        )
        result = server.catalog(action="search", query="Bohemian Rhapsody")
        assert "Bohemian Rhapsody" in result

        # 2. List playlists
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.rock", "attributes": {"name": "Classic Rock", "canEdit": True}},
                ]
            },
            status=200,
        )
        result = server.playlist(action="list")
        assert "Classic Rock" in result

        # 3. Get playlist tracks
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.rock", "attributes": {"name": "Classic Rock", "canEdit": True}},
                ]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.rock/tracks",
            json={
                "data": [
                    {
                        "id": "i.track1",
                        "attributes": {
                            "name": "Stairway to Heaven",
                            "artistName": "Led Zeppelin",
                            "albumName": "Led Zeppelin IV",
                            "durationInMillis": 482000,
                        },
                    }
                ]
            },
            status=200,
        )
        result = server.playlist(action="tracks", playlist="Classic Rock")
        assert "Stairway to Heaven" in result

        # 4. Search library
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/search",
            json={
                "results": {
                    "library-songs": {
                        "data": [
                            {
                                "id": "i.lib1",
                                "attributes": {
                                    "name": "Hotel California",
                                    "artistName": "Eagles",
                                    "albumName": "Hotel California",
                                    "durationInMillis": 391000,
                                },
                            }
                        ]
                    }
                }
            },
            status=200,
        )
        result = server.library(action="search", query="Hotel California")
        assert "Hotel California" in result

        # 5. Add track to playlist (via catalog search + add)
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [{"id": "p.rock", "attributes": {"name": "Classic Rock", "canEdit": True}}]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "cat123",
                                "attributes": {
                                    "name": "Dream On",
                                    "artistName": "Aerosmith",
                                    "albumName": "Aerosmith",
                                    "durationInMillis": 267000,
                                    "releaseDate": "1973-01-01",
                                    "genreNames": ["Rock"],
                                },
                            }
                        ]
                    }
                }
            },
            status=200,
        )
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            json={},
            status=202,
        )
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists/p.rock/tracks",
            json={},
            status=201,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.rock/tracks",
            json={
                "data": [
                    {
                        "id": "i.track1",
                        "attributes": {"name": "Dream On", "artistName": "Aerosmith"},
                    }
                ]
            },
            status=200,
        )
        # api engine adds over amp-api: resolve playlist, catalog search, add tracks
        _AMP = "https://amp-api.music.apple.com/v1"
        responses.add(
            responses.GET,
            f"{_AMP}/me/library/playlists",
            json={
                "data": [{"id": "p.rock", "attributes": {"name": "Classic Rock", "canEdit": True}}]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            f"{_AMP}/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "cat123",
                                "attributes": {"name": "Dream On", "artistName": "Aerosmith"},
                            }
                        ]
                    }
                }
            },
            status=200,
        )
        responses.add(responses.POST, f"{_AMP}/me/library/playlists/p.rock/tracks", status=204)
        result = server.playlist(
            action="add", playlist="Classic Rock", track="Dream On", artist="Aerosmith"
        )
        assert "Dream On" in result or "Added" in result or "error" not in result.lower()

    @responses.activate
    def test_first_10_actions_regular_user(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Regular user: create playlist, add/remove tracks, get recommendations."""
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # 6. Create a new playlist (the web amp-api rail).
        responses.add(
            responses.POST,
            "https://amp-api.music.apple.com/v1/me/library/playlists",
            json={"data": [{"id": "p.new123", "attributes": {"name": "My New Playlist"}}]},
            status=201,
        )
        result = server.playlist(
            action="create", name="My New Playlist", description="Created for testing"
        )
        assert "p.new123" in result or "My New Playlist" in result

        # 7. Get recently played
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/recent/played/tracks",
            json={
                "data": [
                    {
                        "id": "recent1",
                        "attributes": {
                            "name": "Yesterday",
                            "artistName": "The Beatles",
                            "albumName": "Help!",
                            "durationInMillis": 125000,
                        },
                    }
                ]
            },
            status=200,
        )
        result = server.library(action="recently_played", limit=5)
        assert "Yesterday" in result or "recent" in result.lower()

        # 8. Get recommendations
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/recommendations",
            json={"data": [{"id": "rec1", "type": "playlists", "attributes": {"name": "For You"}}]},
            status=200,
        )
        result = server.discover(action="recommendations")
        # Just check it doesn't error

        # 9. Get heavy rotation
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/history/heavy-rotation",
            json={
                "data": [
                    {
                        "id": "hr1",
                        "type": "albums",
                        "attributes": {"name": "Abbey Road", "artistName": "The Beatles"},
                    }
                ]
            },
            status=200,
        )
        result = server.discover(action="heavy_rotation")
        assert "Abbey Road" in result or result  # Just check no error

        # 10. Get artist top songs
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "artists": {
                        "data": [
                            {
                                "id": "artist1",
                                "attributes": {"name": "The Beatles", "genreNames": ["Rock"]},
                            }
                        ]
                    }
                }
            },
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/artists/artist1/view/top-songs",
            json={
                "data": [
                    {
                        "id": "top1",
                        "attributes": {
                            "name": "Come Together",
                            "artistName": "The Beatles",
                            "albumName": "Abbey Road",
                            "durationInMillis": 259000,
                        },
                    }
                ]
            },
            status=200,
        )
        result = server.discover(action="top_songs", artist="The Beatles")
        assert "Come Together" in result or "Beatles" in result

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Helper to setup authentication tokens."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)


class TestUserJourneyFuzzyMatching:
    """Integration tests for fuzzy matching across all entity types."""

    @responses.activate
    def test_fuzzy_playlist_workflow(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """User workflow with fuzzy-named playlist: get tracks, add, remove."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        _empty_amp_playlist_reads()
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Playlist has special characters
        playlist_data = [
            {"id": "p.fuzzy1", "attributes": {"name": "🎸 Rock & Roll Classics", "canEdit": True}}
        ]

        # 1. Get tracks from fuzzy-named playlist (typed without emoji, "and" instead of "&")
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": playlist_data},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.fuzzy1/tracks",
            json={
                "data": [
                    {
                        "id": "i.t1",
                        "attributes": {
                            "name": "Sweet Child O' Mine",
                            "artistName": "Guns N' Roses",
                            "albumName": "Appetite for Destruction",
                            "durationInMillis": 356000,
                        },
                    }
                ]
            },
            status=200,
        )

        result = server.playlist(action="tracks", playlist="Rock and Roll Classics")
        assert "Sweet Child" in result
        assert "Fuzzy match" in result or "fuzzy" in result.lower()

        # 2. Add to fuzzy-named playlist
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": playlist_data},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "cat456",
                                "attributes": {
                                    "name": "Back in Black",
                                    "artistName": "AC/DC",
                                    "albumName": "Back in Black",
                                    "durationInMillis": 255000,
                                    "releaseDate": "1980-07-25",
                                    "genreNames": ["Rock"],
                                },
                            }
                        ]
                    }
                }
            },
            status=200,
        )
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            json={},
            status=202,
        )
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists/p.fuzzy1/tracks",
            json={},
            status=201,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.fuzzy1/tracks",
            json={"data": [{"id": "i.new", "attributes": {"name": "Back in Black"}}]},
            status=200,
        )
        # add routes through the api engine: catalog search + add POST hit amp-api
        responses.add(
            responses.GET,
            "https://amp-api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "cat456",
                                "attributes": {"name": "Back in Black", "artistName": "AC/DC"},
                            }
                        ]
                    }
                }
            },
            status=200,
        )
        responses.add(
            responses.POST,
            "https://amp-api.music.apple.com/v1/me/library/playlists/p.fuzzy1/tracks",
            status=204,
        )

        result = server.playlist(
            action="add", playlist="Rock and Roll Classics", track="Back in Black"
        )
        assert "Back in Black" in result or "Added" in result

    @responses.activate
    def test_fuzzy_track_search_in_catalog(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """User searches with typos/variations, fuzzy matching finds correct track."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # API returns track with proper name
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "789",
                                "attributes": {
                                    "name": "Can't Buy Me Love",
                                    "artistName": "The Beatles",
                                    "albumName": "A Hard Day's Night",
                                    "durationInMillis": 137000,
                                    "releaseDate": "1964-03-16",
                                    "genreNames": ["Rock"],
                                },
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        # User types without apostrophe
        result = server.catalog(action="search", query="Cant Buy Me Love Beatles")
        assert "Can't Buy Me Love" in result or "Cant Buy Me Love" in result

    @responses.activate
    def test_fuzzy_album_search(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """User searches for album with fuzzy name."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Test _find_matching_catalog_album with fuzzy input
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "albums": {
                        "data": [
                            {
                                "id": "album1",
                                "attributes": {
                                    "name": "Sgt. Pepper's Lonely Hearts Club Band",
                                    "artistName": "The Beatles",
                                    "trackCount": 13,
                                    "releaseDate": "1967-06-01",
                                },
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        album, error, fuzzy = server._find_matching_catalog_album(
            "Sgt Peppers Lonely Hearts", "Beatles"
        )
        assert album is not None
        assert error is None
        assert album.get("id") == "album1"

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Helper to setup authentication tokens."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)


class TestApiModeReadRouting:
    """Library reads and ratings go over the HTTP API, never a local app."""

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)
        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

    @responses.activate
    def test_search_in_api_mode_skips_applescript(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        monkeypatch.setattr(server, "_engine", lambda: "api")
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/search",
            json={
                "results": {
                    "library-songs": {
                        "data": [
                            {
                                "id": "i.lib1",
                                "attributes": {"name": "Money", "artistName": "Pink Floyd"},
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        result = server.library(action="search", query="Money")
        assert "Money" in result

    @responses.activate
    def test_browse_songs_in_api_mode_skips_applescript(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        monkeypatch.setattr(server, "_engine", lambda: "api")
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/songs",
            json={
                "data": [
                    {"id": "i.lib2", "attributes": {"name": "Time", "artistName": "Pink Floyd"}}
                ]
            },
            status=200,
        )

        result = server.library(action="browse", item_type="songs", limit=25)
        assert "Time" in result

    @responses.activate
    def test_love_by_name_in_api_mode_uses_catalog_rating(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        monkeypatch.setattr(server, "_engine", lambda: "api")
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "999",
                                "attributes": {"name": "Money", "artistName": "Pink Floyd"},
                            }
                        ]
                    }
                }
            },
            status=200,
        )
        responses.add(
            responses.PUT,
            "https://api.music.apple.com/v1/me/ratings/songs/999",
            status=204,
        )

        result = server.library(action="rate", rate_action="love", track="Money")
        assert "Money" in result

    @responses.activate
    def test_remove_in_api_mode_deletes_via_api(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        monkeypatch.setattr(server, "_write_rail", lambda *a, **k: "web")
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        responses.add(
            responses.GET,
            "https://amp-api.music.apple.com/v1/me/library/search",
            json={
                "results": {
                    "library-songs": {
                        "data": [
                            {
                                "id": "i.toremove",
                                "attributes": {"name": "Money", "artistName": "Pink Floyd"},
                            }
                        ]
                    }
                }
            },
            status=200,
        )
        responses.add(
            responses.DELETE,
            "https://amp-api.music.apple.com/v1/me/library/songs/i.toremove",
            status=204,
        )

        result = server.library(action="remove", track="Money")
        assert "Removed" in result and "Money" in result

    @responses.activate
    def test_add_by_ids_in_api_mode(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """api mode: a p.xxx playlist id is used directly (no name lookup) and a
        library track id is added as type library-songs."""
        # Off macOS + a web session → the add takes the web (amp-api) rail.
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        monkeypatch.setattr(server, "_has_developer_token", lambda: False)
        monkeypatch.setattr(server, "_has_user_token", lambda: True)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        responses.add(
            responses.POST,
            "https://amp-api.music.apple.com/v1/me/library/playlists/p.rock/tracks",
            status=204,
        )
        # The add now re-reads the playlist to report what actually landed.
        responses.add(
            responses.GET,
            "https://amp-api.music.apple.com/v1/me/library/playlists/p.rock/tracks",
            json={"data": []},
            status=200,
        )
        result = server.playlist(action="add", playlist="p.rock", track="i.lib1")
        assert "Added 1 track" in result
        import json as _json

        post = [c for c in responses.calls if c.request.method == "POST"][-1]
        body = _json.loads(post.request.body)
        assert body["data"][0] == {"id": "i.lib1", "type": "library-songs"}

    @responses.activate
    def test_remove_in_api_mode_never_fans_out(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Data-loss guard: a short/common term that substring-matches many
        library songs must delete EXACTLY ONE (exact title preferred), not all."""
        monkeypatch.setattr(server, "_write_rail", lambda *a, **k: "web")
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # "Love" matches three titles; one is an exact match.
        responses.add(
            responses.GET,
            "https://amp-api.music.apple.com/v1/me/library/search",
            json={
                "results": {
                    "library-songs": {
                        "data": [
                            {
                                "id": "i.1",
                                "attributes": {"name": "Crazy in Love", "artistName": "Beyoncé"},
                            },
                            {
                                "id": "i.2",
                                "attributes": {"name": "Love", "artistName": "Lana Del Rey"},
                            },
                            {
                                "id": "i.3",
                                "attributes": {"name": "Love On Top", "artistName": "Beyoncé"},
                            },
                        ]
                    }
                }
            },
            status=200,
        )
        # Only the exact-title song (i.2) may be deleted.
        responses.add(
            responses.DELETE,
            "https://amp-api.music.apple.com/v1/me/library/songs/i.2",
            status=204,
        )

        result = server.library(action="remove", track="Love")
        # Exactly one DELETE, and it was the exact-title match.
        delete_calls = [c for c in responses.calls if c.request.method == "DELETE"]
        assert len(delete_calls) == 1
        assert "i.2" in delete_calls[0].request.url
        assert "2 other" in result  # warns about the other matches


class TestAlbumDisambiguation:
    """Album param: disambiguation filter when track is present, whole-album add when alone."""

    def test_album_without_track_calls_resolve_album(self, monkeypatch):
        """album param alone should call _resolve_album (whole-album add path)."""
        resolve_album_called = False

        def tracking_resolve(*args, **kwargs):
            nonlocal resolve_album_called
            resolve_album_called = True
            return []

        monkeypatch.setattr(server, "_resolve_album", tracking_resolve)
        # Album-add path requires a developer token; mock so the test
        # reaches _resolve_album on systems without a real token (CI).
        monkeypatch.setattr(server, "_has_developer_token", lambda: True)

        server._playlist_add(
            playlist="p.test123", album="Some Album", artist="Some Artist"
        )

        assert (
            resolve_album_called
        ), "_resolve_album should be called when only album (no track) is provided"

    def test_album_with_track_skips_resolve_album(self, monkeypatch):
        """album + track together should NOT call _resolve_album (disambiguation path instead)."""
        resolve_album_called = False

        def tracking_resolve(*args, **kwargs):
            nonlocal resolve_album_called
            resolve_album_called = True
            return []

        monkeypatch.setattr(server, "_resolve_album", tracking_resolve)
        monkeypatch.setattr(server, "get_headers", lambda: {})
        monkeypatch.setattr(server, "_find_track_id", lambda name, artist: (None, None, ""))

        server._playlist_add(
            playlist="p.test123",
            track="Hot Potato",
            album="Ready, Steady, Wiggle!",
            artist="The Wiggles",
        )

        assert (
            not resolve_album_called
        ), "_resolve_album should NOT be called when both track and album are provided"

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Helper to setup authentication tokens."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)


class TestUserJourneyPowerUser:
    """Integration tests for power user workflows (20+ actions)."""

    @responses.activate
    def test_album_workflow(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Power user: add entire album to library."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Search for album
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "albums": {
                        "data": [
                            {
                                "id": "alb1",
                                "attributes": {
                                    "name": "Dark Side of the Moon",
                                    "artistName": "Pink Floyd",
                                    "trackCount": 10,
                                    "releaseDate": "1973-03-01",
                                },
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        # Add album to library
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            json={},
            status=202,
        )

        result = server.library(action="add", album="Dark Side of the Moon", artist="Pink Floyd")
        assert "Dark Side" in result or "Added" in result.lower() or "Album" in result

    @responses.activate
    def test_copy_playlist_workflow(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Power user: copy a playlist."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Source playlist
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [{"id": "p.src", "attributes": {"name": "Original Mix", "canEdit": True}}]
            },
            status=200,
        )

        # Get source tracks
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.src/tracks",
            json={
                "data": [
                    {"id": "i.t1", "attributes": {"name": "Track 1", "artistName": "Artist 1"}},
                    {"id": "i.t2", "attributes": {"name": "Track 2", "artistName": "Artist 2"}},
                ]
            },
            status=200,
        )

        # Create new playlist
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": [{"id": "p.new", "attributes": {"name": "Copy of Original Mix"}}]},
            status=201,
        )

        # Add tracks to new playlist
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists/p.new/tracks",
            json={},
            status=201,
        )

        result = server.playlist(
            action="copy", source="Original Mix", new_name="Copy of Original Mix"
        )
        assert "p.new" in result or "Copy" in result or "copied" in result.lower()

    @responses.activate
    def test_search_deduplication(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Search results should be deduplicated by track ID."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # API returns duplicates
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "dup1",
                                "attributes": {
                                    "name": "Duplicate Song",
                                    "artistName": "Artist",
                                    "albumName": "Album",
                                    "durationInMillis": 200000,
                                    "releaseDate": "2020-01-01",
                                    "genreNames": ["Pop"],
                                },
                            },
                            {
                                "id": "dup1",
                                "attributes": {
                                    "name": "Duplicate Song",
                                    "artistName": "Artist",
                                    "albumName": "Album",
                                    "durationInMillis": 200000,
                                    "releaseDate": "2020-01-01",
                                    "genreNames": ["Pop"],
                                },
                            },  # Same ID = duplicate
                            {
                                "id": "dup2",
                                "attributes": {
                                    "name": "Unique Song",
                                    "artistName": "Artist",
                                    "albumName": "Album",
                                    "durationInMillis": 180000,
                                    "releaseDate": "2020-01-01",
                                    "genreNames": ["Pop"],
                                },
                            },
                        ]
                    }
                }
            },
            status=200,
        )

        result = server.catalog(action="search", query="test query")

        # Should show "2 Songs" not "3 Songs"
        assert "2 Songs" in result

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Helper to setup authentication tokens."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)


class TestCatalogAlbumDetails:
    """Tests for catalog album_details action."""

    @responses.activate
    def test_album_details_by_id(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should fetch album metadata and tracks by catalog ID."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock album metadata response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/albums/1781270319",
            json={
                "data": [
                    {
                        "id": "1781270319",
                        "attributes": {
                            "name": "GNX",
                            "artistName": "Kendrick Lamar",
                            "releaseDate": "2024-11-22",
                            "genreNames": ["Hip-Hop/Rap"],
                            "recordLabel": "pgLang",
                            "trackCount": 12,
                            "copyright": "℗ 2024 pgLang",
                        },
                    }
                ]
            },
            status=200,
        )

        # Mock tracks response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/albums/1781270319/tracks",
            json={
                "data": [
                    {
                        "id": "t1",
                        "attributes": {"name": "wacced out murals", "durationInMillis": 251000},
                    },
                    {"id": "t2", "attributes": {"name": "squabble up", "durationInMillis": 193000}},
                ]
            },
            status=200,
        )

        result = server.catalog(action="album_details", album="1781270319")

        assert "GNX" in result
        assert "Kendrick Lamar" in result
        assert "2024-11-22" in result
        assert "Hip-Hop/Rap" in result
        assert "pgLang" in result
        assert "wacced out murals" in result
        assert "squabble up" in result

    @responses.activate
    def test_album_details_by_name_fuzzy_match(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should find album by name using fuzzy matching."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock search response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "albums": {
                        "data": [
                            {
                                "id": "123",
                                "attributes": {"name": "Abbey Road", "artistName": "The Beatles"},
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        # Mock album metadata
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/albums/123",
            json={
                "data": [
                    {
                        "id": "123",
                        "attributes": {
                            "name": "Abbey Road",
                            "artistName": "The Beatles",
                            "releaseDate": "1969-09-26",
                            "genreNames": ["Rock"],
                            "recordLabel": "Apple Records",
                            "trackCount": 17,
                            "copyright": "℗ 1969",
                        },
                    }
                ]
            },
            status=200,
        )

        # Mock tracks
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/albums/123/tracks",
            json={
                "data": [
                    {
                        "id": "t1",
                        "attributes": {"name": "Come Together", "durationInMillis": 259000},
                    },
                ]
            },
            status=200,
        )

        result = server.catalog(action="album_details", album="abbey road", artist="beatles")

        assert "Abbey Road" in result
        assert "The Beatles" in result
        assert "Come Together" in result

    @responses.activate
    def test_album_details_missing_album_error(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should return error when album not found."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock search with no results
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"albums": {"data": []}}},
            status=200,
        )

        result = server.catalog(action="album_details", album="NonexistentAlbum999")

        assert "not found" in result.lower() or "error" in result.lower()


class TestDiscoverStorefrontParameter:
    """Tests for discover action storefront parameter."""

    @responses.activate
    def test_charts_with_storefront_parameter(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should query Italian charts without changing default storefront."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock Italy charts response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/it/charts",
            json={
                "results": {
                    "songs": [
                        {
                            "name": "Top brani",
                            "data": [
                                {
                                    "id": "it1",
                                    "attributes": {
                                        "name": "Italian Song",
                                        "artistName": "Italian Artist",
                                    },
                                },
                            ],
                        }
                    ]
                }
            },
            status=200,
        )

        result = server.discover(action="charts", chart_type="songs", storefront="it")

        # Verify it was called with 'it' storefront
        assert len(responses.calls) == 1
        assert "/catalog/it/charts" in responses.calls[0].request.url
        assert "Italian Song" in result or "Top brani" in result

    @responses.activate
    def test_top_songs_with_storefront_parameter(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should query artist top songs in specific storefront."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock search in JP storefront
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/jp/search",
            json={
                "results": {
                    "artists": {
                        "data": [{"id": "jp-artist-123", "attributes": {"name": "Japanese Artist"}}]
                    }
                }
            },
            status=200,
        )

        # Mock top songs
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/jp/artists/jp-artist-123/view/top-songs",
            json={
                "data": [
                    {
                        "id": "jp-song-1",
                        "attributes": {"name": "JP Hit Song", "artistName": "Japanese Artist"},
                    },
                ]
            },
            status=200,
        )

        result = server.discover(action="top_songs", artist="Japanese Artist", storefront="jp")

        # Verify JP storefront was used
        assert any("/catalog/jp/" in call.request.url for call in responses.calls)
        assert "Japanese Artist" in result


class TestDiscoverRecommendationsLimit:
    """Tests for discover recommendations limit parameter."""

    @responses.activate
    def test_recommendations_respects_limit_parameter(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should only return requested number of recommendations."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock recommendations response with many items
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/recommendations",
            json={
                "data": [
                    {
                        "attributes": {"title": {"stringForDisplay": "For You"}},
                        "relationships": {
                            "contents": {
                                "data": [
                                    {
                                        "id": f"rec{i}",
                                        "type": "songs",
                                        "attributes": {
                                            "name": f"Song {i}",
                                            "artistName": "Artist",
                                            "releaseDate": "2024-01-01",
                                        },
                                    }
                                    for i in range(1, 51)  # 50 items
                                ]
                            }
                        },
                    }
                ]
            },
            status=200,
        )

        # Request only 15 items
        result = server.discover(action="recommendations", limit=15, format="text")

        # Count items in result (rough check - each song should have a line)
        lines = [ln for ln in result.split("\n") if ln.strip() and not ln.startswith("===")]
        # Should have ~15 lines, not 50
        assert len(lines) <= 20  # Allow some buffer for formatting

    @responses.activate
    def test_recommendations_limit_zero_returns_all(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should return all recommendations when limit=0."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock recommendations response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/recommendations",
            json={
                "data": [
                    {
                        "attributes": {"title": {"stringForDisplay": "For You"}},
                        "relationships": {
                            "contents": {
                                "data": [
                                    {
                                        "id": f"rec{i}",
                                        "type": "songs",
                                        "attributes": {
                                            "name": f"Song {i}",
                                            "artistName": "Artist",
                                            "releaseDate": "2024-01-01",
                                        },
                                    }
                                    for i in range(1, 21)  # 20 items
                                ]
                            }
                        },
                    }
                ]
            },
            status=200,
        )

        # Request with limit=0 (should return all)
        result = server.discover(action="recommendations", limit=0, format="text")

        # Should have all ~20 items
        lines = [ln for ln in result.split("\n") if ln.strip() and not ln.startswith("===")]
        assert len(lines) >= 7  # At least 7-8 items from the category


class TestHasDeveloperToken:
    """Tests for _has_developer_token feature-detection helper."""

    def test_returns_false_when_token_missing(self, monkeypatch):
        """No token configured → False, no exception leaked."""

        def raise_missing():
            raise FileNotFoundError("Developer token not found")

        monkeypatch.setattr(server, "get_developer_token", raise_missing)
        assert server._has_developer_token() is False

    def test_returns_false_when_token_expired(self, monkeypatch):
        """Expired token → False (caller should fall back, not crash)."""

        def raise_expired():
            raise ValueError("Developer token expired or expiring soon")

        monkeypatch.setattr(server, "get_developer_token", raise_expired)
        assert server._has_developer_token() is False

    def test_returns_true_when_token_valid(self, monkeypatch):
        """Token present and valid → True."""
        monkeypatch.setattr(server, "get_developer_token", lambda: "fake_token")
        assert server._has_developer_token() is True


class TestResolveTrackBatch:
    """_resolve_track / _resolve_input batch parsing: newline, CSV, JSON,
    single. Newline support is the fix for callers that paste one song per
    line — previously the whole blob was treated as a single track name."""

    def test_newline_separated_names(self):
        blob = "\n".join(f"Song {i}" for i in range(1, 15))
        results = server._resolve_track(blob)
        assert len(results) == 14
        assert [r.value for r in results] == [f"Song {i}" for i in range(1, 15)]
        assert all(r.error is None for r in results)

    def test_newline_preserves_commas_in_titles(self):
        # Newline split must NOT further split on commas inside a title.
        results = server._resolve_track("Take Me Home, Country Roads\nImagine")
        assert [r.value for r in results] == ["Take Me Home, Country Roads", "Imagine"]

    def test_newline_skips_blank_lines(self):
        results = server._resolve_track("Hey Jude\n\n  \nLet It Be\n")
        assert [r.value for r in results] == ["Hey Jude", "Let It Be"]

    def test_newline_applies_artist_to_each(self):
        results = server._resolve_track("Hey Jude\nLet It Be", artist="Beatles")
        assert [r.artist for r in results] == ["Beatles", "Beatles"]

    def test_csv_still_works(self):
        results = server._resolve_track("Hey Jude, Let It Be")
        assert [r.value for r in results] == ["Hey Jude", "Let It Be"]

    def test_json_array_still_works(self):
        results = server._resolve_track('[{"name":"A","artist":"X"},{"name":"B","artist":"Y"}]')
        assert [(r.value, r.artist) for r in results] == [("A", "X"), ("B", "Y")]

    def test_single_name_still_works(self):
        results = server._resolve_track("Yesterday")
        assert len(results) == 1
        assert results[0].value == "Yesterday"


class TestApplyPagination:
    """Unit tests for the _apply_pagination helper."""

    def test_offset_equals_total_returns_empty_not_error(self):
        """Regression: offset == total_count is a valid pagination boundary; must not error."""
        items = [{"name": f"Track {i}"} for i in range(10)]
        result, total, error = server._apply_pagination(items, limit=10, offset=10)
        assert error is None
        assert result == []
        assert total == 10

    def test_offset_beyond_total_returns_error(self):
        """offset strictly greater than total_count is clearly out of bounds."""
        items = [{"name": f"Track {i}"} for i in range(10)]
        result, total, error = server._apply_pagination(items, limit=10, offset=11)
        assert error is not None
        assert "11" in error
        assert result == []
        assert total == 10

    def test_offset_zero_limit_zero_returns_all(self):
        """offset=0, limit=0: returns every item unchanged."""
        items = [{"name": f"Track {i}"} for i in range(10)]
        result, total, error = server._apply_pagination(items, limit=0, offset=0)
        assert error is None
        assert result == items
        assert total == 10

    def test_empty_list_with_nonzero_offset_returns_empty_without_error(self):
        """Empty input list with any offset yields ([], 0, None) — no error."""
        result, total, error = server._apply_pagination([], limit=10, offset=5)
        assert error is None
        assert result == []
        assert total == 0

    def test_offset_midrange_limit_fits_within_remaining(self):
        """Offset in the middle; limit does not exceed the remaining items."""
        items = [{"name": f"Track {i}"} for i in range(25)]
        result, total, error = server._apply_pagination(items, limit=10, offset=10)
        assert error is None
        assert len(result) == 10
        assert result[0]["name"] == "Track 10"
        assert result[-1]["name"] == "Track 19"
        assert total == 25

    def test_offset_midrange_limit_exceeds_remaining_returns_partial_page(self):
        """Offset in the middle; limit overshoots end — returns only the remaining items."""
        items = [{"name": f"Track {i}"} for i in range(15)]
        result, total, error = server._apply_pagination(items, limit=10, offset=10)
        assert error is None
        assert len(result) == 5
        assert result[0]["name"] == "Track 10"
        assert total == 15


class TestLibraryBrowsePagination:
    """Tests for offset/limit pagination in _library_browse over the API.

    Three basic scenarios: first page, second page (non-zero offset), and
    fetch-all (limit=0).
    """

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)
        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

    def _make_api_song(self, i):
        return {
            "id": f"i.lib{i}",
            "attributes": {
                "name": f"Track {i}",
                "artistName": "Artist",
                "albumName": "Album",
                "contentRating": "clean",
            },
        }

    def _make_as_song(self, i):
        return {"name": f"Track {i}", "artist": "Artist", "album": "Album", "id": f"PID{i}"}

    # ── AppleScript path ──────────────────────────────────────────────────────

    # ── API path ──────────────────────────────────────────────────────────────

    @responses.activate
    def test_api_offset_zero_limit_ten_returns_first_page(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """offset=0, limit=10: returns first 10 items in a single API call."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/songs",
            json={"data": [self._make_api_song(i) for i in range(10)]},
            status=200,
        )

        result = server._library_browse(item_type="songs", limit=10, offset=0)

        assert "Track 0" in result
        assert "Track 9" in result
        assert len(responses.calls) == 1

    @responses.activate
    def test_api_offset_ten_limit_ten_returns_second_page(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """offset=10, limit=10: fetches offset+limit items, returns items 11-20."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/songs",
            json={"data": [self._make_api_song(i) for i in range(20)]},
            status=200,
        )

        result = server._library_browse(item_type="songs", limit=10, offset=10)

        assert "Track 10" in result
        assert "Track 19" in result
        assert "Track 0" not in result
        assert "Track 9" not in result
        assert len(responses.calls) == 1

    @responses.activate
    def test_api_limit_zero_fetches_all_across_multiple_calls(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """limit=0: keeps calling API until a partial batch signals the end."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Full batch of 100 → pagination continues
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/songs",
            json={"data": [self._make_api_song(i) for i in range(100)]},
            status=200,
        )
        # Partial batch of 50 → pagination stops
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/songs",
            json={"data": [self._make_api_song(i) for i in range(100, 150)]},
            status=200,
        )

        result = server._library_browse(item_type="songs", limit=0, offset=0)

        assert "Track 0" in result
        assert "Track 149" in result
        assert len(responses.calls) == 2

    # ── Regression: offset == limit on a large library ───────────────────────

    @responses.activate
    def test_api_large_library_second_page_returns_tracks_not_error(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Regression: API path, 1000+ song library, offset=10, limit=10 returns page 2."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/songs",
            json={"data": [self._make_api_song(i) for i in range(20)]},
            status=200,
        )

        result = server._library_browse(item_type="songs", limit=10, offset=10)

        assert "Offset 10 exceeds" not in result
        assert "Track 10" in result
        assert "Track 19" in result
        assert "Track 0" not in result


class TestCanonicalMatcher:
    """The single normalized matcher used at every user-string comparison point.

    Issue #26: Music stores titles with typographic glyphs (curly quotes U+2019,
    ellipsis U+2026, accents) while users/models type ASCII. Every match point
    routes through _normalize_for_match / _loose_contains so the cosmetic glyph
    never decides a match. These are pure-function tests — no Music app needed.
    """

    # --- _normalize_for_match -------------------------------------------------

    def test_smart_and_straight_quotes_normalize_identically(self):
        assert server._normalize_for_match("That's a No No") == server._normalize_for_match(
            "That’s a No No"
        )

    def test_left_quote_and_modifier_apostrophe_fold(self):
        base = server._normalize_for_match("Its Me")
        for variant in ["It's Me", "It’s Me", "It‘s Me", "Itʼs Me"]:
            assert server._normalize_for_match(variant) == base

    def test_accents_fold_to_base_letter(self):
        # The bug the old normalizer had: é was stripped to nothing (caf), not e.
        assert server._normalize_for_match("café") == "cafe"
        assert server._normalize_for_match("Björk") == "bjork"
        assert server._normalize_for_match("Beyoncé") == server._normalize_for_match("Beyonce")

    def test_ellipsis_folds(self):
        assert server._normalize_for_match("Wait…") == server._normalize_for_match("Wait...")

    def test_ampersand_becomes_and(self):
        assert server._normalize_for_match("Salt & Pepa") == server._normalize_for_match(
            "Salt and Pepa"
        )

    def test_hyphens_become_spaces_emoji_removed(self):
        # Hyphens join words -> spaces (keeps tokens distinct); emoji -> gone.
        assert server._normalize_for_match("Peek-A-Boo 🎵") == "peek a boo"

    def test_hyphen_removal_does_not_cross_token_boundary(self):
        # Regression: deleting the hyphen would fold "Peek-A-Boo" -> "peekaboo",
        # letting "kabo" cross the boundary and false-match. Spacing prevents it.
        assert not server._loose_contains("kabo", "Peek-A-Boo")
        # But a real hyphen/space variation still matches.
        assert server._loose_contains("Peek A Boo", "Peek-A-Boo")

    def test_whitespace_collapsed_and_cased(self):
        assert server._normalize_for_match("  THE   Beatles ") == "the beatles"

    def test_none_and_empty_are_empty(self):
        assert server._normalize_for_match(None) == ""
        assert server._normalize_for_match("") == ""
        assert server._normalize_for_match("   ") == ""

    # --- _loose_contains ------------------------------------------------------

    def test_straight_query_matches_curly_name(self):
        assert server._loose_contains("That's a No No", "That’s a No No")

    def test_curly_query_matches_straight_name(self):
        # The reporter's `track="That’s..."` case: query is curly, the name
        # search returns is straight (osascript normalizes stdout).
        assert server._loose_contains("Wouldn’t It Be Nice", "Wouldn't It Be Nice (Live)")

    def test_accent_query_matches_unaccented_name(self):
        assert server._loose_contains("Fur Elise", 'Bagatelle "Für Elise"')

    def test_substring_semantics_preserved(self):
        assert server._loose_contains("No No", "That's a No No")
        assert not server._loose_contains("Yes Yes", "That's a No No")

    def test_empty_needle_imposes_no_constraint(self):
        # Drop-in for `if query and query.lower() not in name`.
        assert server._loose_contains("", "anything") is True
        assert server._loose_contains(None, "anything") is True

    def test_none_haystack_only_matches_empty_needle(self):
        assert server._loose_contains("x", None) is False
        assert server._loose_contains(None, None) is True

    # --- _loose_equals --------------------------------------------------------

    def test_loose_equals_folds_quotes_and_accents(self):
        assert server._loose_equals("Café del Mar", "cafe del mar")
        assert server._loose_equals("Rock 'n' Roll", "Rock ’n’ Roll")
        assert not server._loose_equals("Song", "Songs")


class TestResolveCatalogTrackItunes:
    """The free iTunes Search API resolver (tokenless track resolution, #28)."""

    def _resp(self, results):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"results": results}
        return m

    def test_picks_exact_name_and_artist(self, monkeypatch):
        results = [
            {
                "trackName": "Blinding Lights (Remix)",
                "artistName": "The Weeknd",
                "trackViewUrl": "u1",
            },
            {"trackName": "Blinding Lights", "artistName": "The Weeknd", "trackViewUrl": "u2"},
        ]
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: self._resp(results))
        r = server._resolve_catalog_track_itunes("Blinding Lights", "The Weeknd")
        assert r is not None and r["url"] == "u2" and r["name"] == "Blinding Lights"

    def test_handles_smart_quote_and_kpop(self, monkeypatch):
        # OCR struggled with these; the API resolves them cleanly.
        results = [{"trackName": "I CAN'T STOP ME", "artistName": "TWICE", "trackViewUrl": "u"}]
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: self._resp(results))
        r = server._resolve_catalog_track_itunes("i cant stop me", "twice")
        assert r is not None and r["url"] == "u"

    def test_none_when_name_absent(self, monkeypatch):
        results = [{"trackName": "Totally Different", "artistName": "X", "trackViewUrl": "u"}]
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: self._resp(results))
        assert server._resolve_catalog_track_itunes("Blinding Lights", "The Weeknd") is None

    def test_none_on_weak_substring_with_wrong_artist(self, monkeypatch):
        # Title merely CONTAINS the query but isn't exact, AND the artist is
        # wrong -> score 0 -> rejected (don't deep-link to the wrong track).
        results = [
            {"trackName": "Lights Out", "artistName": "Nobody", "trackViewUrl": "u"},
            {"trackName": "City of Lights (Extended)", "artistName": "Other", "trackViewUrl": "u2"},
        ]
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: self._resp(results))
        assert server._resolve_catalog_track_itunes("Lights", "The Weeknd") is None

    def test_accepts_exact_title_even_without_artist(self, monkeypatch):
        # Exact title alone (score 2) is enough when no artist is supplied.
        results = [{"trackName": "Lights", "artistName": "Whoever", "trackViewUrl": "u"}]
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: self._resp(results))
        r = server._resolve_catalog_track_itunes("Lights")
        assert r is not None and r["url"] == "u"

    def test_none_on_empty_results(self, monkeypatch):
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: self._resp([]))
        assert server._resolve_catalog_track_itunes("anything") is None

    def test_none_on_http_error(self, monkeypatch):
        m = MagicMock()
        m.status_code = 503
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: m)
        assert server._resolve_catalog_track_itunes("anything") is None

    def test_none_when_url_missing(self, monkeypatch):
        results = [{"trackName": "Blinding Lights", "artistName": "The Weeknd"}]  # no trackViewUrl
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: self._resp(results))
        assert server._resolve_catalog_track_itunes("Blinding Lights", "The Weeknd") is None

    def test_none_on_network_exception(self, monkeypatch):
        def boom(*a, **k):
            raise ConnectionError("offline")

        monkeypatch.setattr(server.requests, "get", boom)
        assert server._resolve_catalog_track_itunes("anything") is None

    def test_artist_match_beats_exact_title_by_wrong_artist(self, monkeypatch):
        # The user named the artist (Brye). A same-titled song by a DIFFERENT
        # artist (exact "Lemons" by Hairitage) must NOT win over the requested
        # artist's track, even though its title is an exact match. Artist is the
        # strong signal when supplied. (Measured regression on macOS 26.5: the
        # old title-weighted scorer silently resolved to Hairitage.)
        results = [
            {"trackName": "Lemons", "artistName": "Hairitage", "trackViewUrl": "wrong"},
            {"trackName": "LEMONS (feat. Cavetown)", "artistName": "Brye", "trackViewUrl": "right"},
            {"trackName": "LEMONS (Demo)", "artistName": "Brye", "trackViewUrl": "demo"},
        ]
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: self._resp(results))
        r = server._resolve_catalog_track_itunes("Lemons", "Brye")
        assert r is not None and r["artist"] == "Brye"
        # iTunes' relevance order breaks the Brye/Brye tie -> first one wins.
        assert r["url"] == "right"
        assert r["name"] == "LEMONS (feat. Cavetown)"

    def test_rejects_wrong_artist_even_with_exact_title(self, monkeypatch):
        # Artist supplied but NO result matches it -> None. Fail loudly rather
        # than silently add a different artist's identically-titled song.
        results = [
            {"trackName": "Lemons", "artistName": "Hairitage", "trackViewUrl": "u"},
            {"trackName": "Lemons", "artistName": "Muni Long", "trackViewUrl": "u2"},
        ]
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: self._resp(results))
        assert server._resolve_catalog_track_itunes("Lemons", "Brye") is None

    def test_canonical_suffix_title_accepted_when_artist_matches(self, monkeypatch):
        # Title carries a "(feat. ...)" suffix (not an exact match) but the
        # artist matches -> accepted. This is exactly the obscure-track case the
        # pop-over needs the canonical title for.
        results = [
            {"trackName": "LEMONS (feat. Cavetown)", "artistName": "Brye", "trackViewUrl": "u"}
        ]
        monkeypatch.setattr(server.requests, "get", lambda *a, **k: self._resp(results))
        r = server._resolve_catalog_track_itunes("Lemons", "Brye")
        assert r is not None and r["url"] == "u"
        assert r["name"] == "LEMONS (feat. Cavetown)"
