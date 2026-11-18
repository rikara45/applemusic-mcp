"""Coverage tests for the queue and playback tools.

Drives _queue_resolve_catalog_id, _format_queue, queue(), the playback()
dispatcher, plus the API helpers it still uses: _catalog_song_name and
_convert_song_url_to_album. Playback and Up Next run only in the Chrome web
player here; the native (AppleScript) helpers are gone.
"""

import pytest
import responses

from applemusic_mcp import server


@pytest.fixture(autouse=True)
def _reset_active_playback():
    """The active-engine hint is a module global; reset it so play/queue tests
    that set it don't leak into later tests that read it."""
    server._active_playback_engine = ""
    yield
    server._active_playback_engine = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_headers(monkeypatch):
    monkeypatch.setattr(server, "get_headers", lambda: {"Authorization": "Bearer TEST"})
    monkeypatch.setattr(server, "get_storefront", lambda: "us")


# ===========================================================================
# Queue tool — remaining uncovered branches
# ===========================================================================


class TestQueueMisc:
    def test_resolve_catalog_id_empty_string_returns_none(self):
        """Line 6864: empty / whitespace-only track -> None."""
        assert server._queue_resolve_catalog_id("") is None
        assert server._queue_resolve_catalog_id("   ") is None

    def test_autoplay_enabled_none_error(self, monkeypatch):
        """Line 6940: autoplay without enabled param."""
        out = server.queue(action="autoplay")
        assert "enabled" in out.lower()

    def test_queue_clear_error_surfaces(self, monkeypatch):
        """Error branch in queue clear — ok=False."""
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "queue_clear", lambda: (False, "not signed in"))
        out = server.queue(action="clear", engine="chrome")
        assert out.startswith("Error:")

    def test_queue_play_next_error_surfaces(self, monkeypatch):
        """ok=False for play_next."""
        monkeypatch.setattr(server.amp_api, "search_catalog_songs", lambda q, n=1: [{"id": "1"}])
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "queue_play_next", lambda cid: (False, "browser offline"))
        out = server.queue(action="play_next", track="X", engine="chrome")
        assert out.startswith("Error:")

    def test_queue_play_last_error_surfaces(self, monkeypatch):
        monkeypatch.setattr(server.amp_api, "search_catalog_songs", lambda q, n=1: [{"id": "1"}])
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "queue_play_later", lambda cid: (False, "offline"))
        out = server.queue(action="play_last", track="X", engine="chrome")
        assert out.startswith("Error:")

    def test_queue_remove_error_surfaces(self, monkeypatch):
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "queue_remove", lambda i: (False, "remove failed"))
        out = server.queue(action="remove", index=0, engine="chrome")
        assert out.startswith("Error:")

    def test_queue_jump_error_surfaces(self, monkeypatch):
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "queue_jump", lambda i: (False, "jump failed"))
        out = server.queue(action="jump", index=0, engine="chrome")
        assert out.startswith("Error:")

    def test_queue_autoplay_error_surfaces(self, monkeypatch):
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "queue_autoplay", lambda e: (False, "autoplay failed"))
        out = server.queue(action="autoplay", enabled=True, engine="chrome")
        assert out.startswith("Error:")


# ===========================================================================
# Playback dispatcher — remaining uncovered branches
# ===========================================================================


class TestPlaybackDispatcher:
    def test_play_routes_to_browser(self, monkeypatch):
        monkeypatch.setattr(server, "_browser_play", lambda *a, **k: "browser played")
        assert server.playback(action="play", track="X") == "browser played"

    def test_play_below_web_level_has_no_engine(self, monkeypatch):
        monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
        monkeypatch.setattr(server, "_browser_play", lambda *a, **k: "browser played")
        out = server.playback(action="play", track="X")
        assert out.startswith("Error:") and "--level web" in out

    def test_engine_override_web_forces_browser(self, monkeypatch):
        """engine='web' picks the Chrome web player."""
        monkeypatch.setattr(server, "_browser_play", lambda *a, **k: "browser played")
        assert server.playback(action="play", track="X", engine="web") == "browser played"

    @pytest.mark.parametrize("engine", ["native", "safari"])
    def test_engine_override_native_or_safari_refused(self, engine):
        """There is no native/Safari player in this build; the override is refused."""
        out = server.playback(action="play", track="X", engine=engine)
        assert out.startswith("Error:") and "engine" in out.lower()

    def test_engine_override_invalid_value_errors(self):
        out = server.playback(action="play", track="X", engine="bogus")
        assert "Error" in out and "engine" in out.lower()

    def test_control_no_control_param_error(self):
        out = server.playback(action="control")
        assert "control param" in out.lower() or "Error" in out

    def test_control_browser_success(self, monkeypatch):
        """control via browser, success — the tail re-renders now_playing."""
        monkeypatch.setattr(server, "_playback_engine", lambda *a, **k: "chrome")
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "playback_control", lambda c, s: (True, "Paused"))
        monkeypatch.setattr(browser, "now_playing", lambda: None)
        out = server.playback(action="control", control="pause")
        assert "Paused" in out

    def test_control_browser_error(self, monkeypatch):
        """Line 7011: control via browser, error branch."""
        monkeypatch.setattr(server, "_playback_engine", lambda *a, **k: "chrome")
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "playback_control", lambda c, s: (False, "offline"))
        out = server.playback(action="control", control="next")
        assert out.startswith("Error:")

    def test_control_below_web_level_needs_web(self, monkeypatch):
        """control at the api level: there is no web player to drive."""
        monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
        out = server.playback(action="control", control="play")
        assert out.startswith("Error:") and "--level web" in out

    def test_now_playing_browser_track_found(self, monkeypatch):
        """now_playing reads the Chrome web player's track."""
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        from applemusic_mcp import browser

        monkeypatch.setattr(
            browser,
            "now_playing",
            lambda: {"name": "Strobe", "artist": "deadmau5", "album": "4x4=12"},
        )
        out = server.playback(action="now_playing")
        assert "Strobe" in out and "deadmau5" in out

    def test_now_playing_browser_nothing(self, monkeypatch):
        """Active Chrome engine, nothing playing → 'Nothing playing (Chrome...)'."""
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "now_playing", lambda: None)
        out = server.playback(action="now_playing")
        assert "Nothing" in out

    def test_now_playing_no_applescript(self, monkeypatch):
        """Default engine, nothing playing → 'Nothing playing (Chrome...)'."""
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "now_playing", lambda: None)
        out = server.playback(action="now_playing")
        assert "Chrome" in out and "Nothing" in out

    def test_settings_browser_success(self, monkeypatch):
        """Lines 7028-7036: settings via browser."""
        monkeypatch.setattr(server, "_playback_engine", lambda *a, **k: "chrome")
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "browser_settings", lambda v, s, r: (True, "Volume set to 80"))
        out = server.playback(action="settings", volume=80)
        assert "Volume" in out

    def test_settings_browser_shuffle_none(self, monkeypatch):
        """Line 7031: shuffle_mode empty -> shuffle_b=None passed to browser."""
        monkeypatch.setattr(server, "_playback_engine", lambda *a, **k: "chrome")
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        from applemusic_mcp import browser

        seen = {}
        monkeypatch.setattr(
            browser, "browser_settings", lambda v, s, r: seen.update(s=s) or (True, "OK")
        )
        server.playback(action="settings")  # no shuffle_mode
        assert seen.get("s") is None

    def test_settings_browser_shuffle_on(self, monkeypatch):
        """Line 7031-7033: shuffle_mode='on' -> True."""
        monkeypatch.setattr(server, "_playback_engine", lambda *a, **k: "chrome")
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        from applemusic_mcp import browser

        seen = {}
        monkeypatch.setattr(
            browser, "browser_settings", lambda v, s, r: seen.update(s=s) or (True, "OK")
        )
        server.playback(action="settings", shuffle_mode="on")
        assert seen.get("s") is True

    def test_settings_browser_error(self, monkeypatch):
        """Line 7036: settings browser returns error."""
        monkeypatch.setattr(server, "_playback_engine", lambda *a, **k: "chrome")
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        from applemusic_mcp import browser

        monkeypatch.setattr(browser, "browser_settings", lambda v, s, r: (False, "oops"))
        out = server.playback(action="settings", volume=50)
        assert out.startswith("Error:")

    def test_settings_below_web_level_needs_web(self, monkeypatch):
        """settings at the api level: there is no web player to drive."""
        monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
        out = server.playback(action="settings", volume=50)
        assert out.startswith("Error:") and "--level web" in out

    def test_reveal_no_name_no_url_error(self):
        """reveal without track name or url."""
        out = server.playback(action="reveal")
        assert "required" in out.lower()

    def test_reveal_browser_resolve_fails(self, monkeypatch):
        """Line 7051: reveal via browser, catalog resolve returns None."""
        monkeypatch.setattr(server, "_playback_engine", lambda *a, **k: "chrome")
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        monkeypatch.setattr(server, "_resolve_catalog_track_itunes", lambda n, a="": None)
        out = server.playback(action="reveal", track="zzznope")
        assert "not found" in out.lower()

    def test_reveal_browser_url_ok(self, monkeypatch):
        """Lines 7044-7054: reveal via browser with a resolved URL."""
        monkeypatch.setattr(server, "_playback_engine", lambda *a, **k: "chrome")
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        from applemusic_mcp import browser as br

        monkeypatch.setattr(
            server,
            "_resolve_catalog_track_itunes",
            lambda n, a="": {"url": "https://music.apple.com/us/song/x/1"},
        )
        monkeypatch.setattr(br, "reveal_url", lambda u: (True, f"Showing: {u}"))
        out = server.playback(action="reveal", track="Strobe")
        assert "Showing" in out

    def test_reveal_browser_url_error(self, monkeypatch):
        """Line 7054: reveal_url returns error."""
        monkeypatch.setattr(server, "_playback_engine", lambda *a, **k: "chrome")
        monkeypatch.setattr(server, "_get_active_playback", lambda: "chrome")
        from applemusic_mcp import browser as br

        monkeypatch.setattr(
            server,
            "_resolve_catalog_track_itunes",
            lambda n, a="": {"url": "https://music.apple.com/us/song/x/1"},
        )
        monkeypatch.setattr(br, "reveal_url", lambda u: (False, "no browser"))
        out = server.playback(action="reveal", track="Strobe")
        assert out.startswith("Error:")

    def test_airplay_needs_music_app(self):
        """AirPlay isn't reachable from the Chrome web player."""
        out = server.playback(action="airplay")
        assert out.startswith("Error:") and "Music.app" in out

    def test_unknown_action_error(self):
        out = server.playback(action="foobar_xyz")
        assert "Unknown" in out


# ===========================================================================
# _catalog_song_name  (lines 7080-7096)
# ===========================================================================


class TestCatalogSongName:
    @responses.activate
    def test_success(self, monkeypatch):
        _mock_headers(monkeypatch)
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/songs/12345",
            json={"data": [{"attributes": {"name": "Strobe"}}]},
            status=200,
        )
        assert server._catalog_song_name("12345") == "Strobe"

    @responses.activate
    def test_non_200_returns_empty(self, monkeypatch):
        _mock_headers(monkeypatch)
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/songs/99999",
            status=404,
        )
        assert server._catalog_song_name("99999") == ""

    @responses.activate
    def test_empty_data_returns_empty(self, monkeypatch):
        _mock_headers(monkeypatch)
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/songs/11111",
            json={"data": []},
            status=200,
        )
        assert server._catalog_song_name("11111") == ""

    @responses.activate
    def test_no_name_attr_returns_empty(self, monkeypatch):
        _mock_headers(monkeypatch)
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/songs/22222",
            json={"data": [{"attributes": {}}]},
            status=200,
        )
        assert server._catalog_song_name("22222") == ""

    def test_exception_returns_empty(self, monkeypatch):
        def _boom():
            raise ValueError("no token")

        monkeypatch.setattr(server, "get_headers", _boom)
        assert server._catalog_song_name("12345") == ""


# ===========================================================================
# _convert_song_url_to_album  (lines 7098-7136)
# ===========================================================================


class TestConvertSongUrlToAlbum:
    def test_no_song_id_in_url_returns_none(self):
        assert server._convert_song_url_to_album("https://music.apple.com/us/album/x/123") is None

    @responses.activate
    def test_success(self, monkeypatch):
        _mock_headers(monkeypatch)
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/songs/99999",
            json={
                "data": [
                    {
                        "attributes": {"albumName": "4x4=12"},
                        "relationships": {"albums": {"data": [{"id": "888888"}]}},
                    }
                ]
            },
            status=200,
        )
        result = server._convert_song_url_to_album("https://music.apple.com/us/song/strobe/99999")
        assert result is not None
        assert "888888" in result
        assert "?i=99999" in result

    @responses.activate
    def test_non_200_returns_none(self, monkeypatch):
        _mock_headers(monkeypatch)
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/songs/77777",
            status=401,
        )
        assert server._convert_song_url_to_album("https://music.apple.com/us/song/x/77777") is None

    @responses.activate
    def test_empty_data_returns_none(self, monkeypatch):
        _mock_headers(monkeypatch)
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/songs/55555",
            json={"data": []},
            status=200,
        )
        assert server._convert_song_url_to_album("https://music.apple.com/us/song/x/55555") is None

    @responses.activate
    def test_no_albums_relationship_returns_none(self, monkeypatch):
        _mock_headers(monkeypatch)
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/songs/44444",
            json={
                "data": [
                    {
                        "attributes": {"albumName": "X"},
                        "relationships": {"albums": {"data": []}},
                    }
                ]
            },
            status=200,
        )
        assert server._convert_song_url_to_album("https://music.apple.com/us/song/x/44444") is None

    def test_exception_returns_none(self, monkeypatch):
        monkeypatch.setattr(server, "get_headers", lambda: (_ for _ in ()).throw(Exception("fail")))
        assert server._convert_song_url_to_album("https://music.apple.com/us/song/x/33333") is None


