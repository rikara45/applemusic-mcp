"""Tests for the playback routing (the Chrome web player is the only engine).

The browser playback primitives themselves drive a live MusicKit instance, so
they're exercised manually; here we cover the routing helpers and the
_browser_play track/url dispatch with the browser module mocked.
"""

from applemusic_mcp import server


def test_use_browser_playback_always_true():
    """Playback always runs in the Chrome web player here."""
    assert server._use_browser_playback() is True


def test_browser_play_with_url(monkeypatch):
    import applemusic_mcp.browser as browser

    calls = {}
    monkeypatch.setattr(
        browser, "play_url", lambda u, s=False: calls.update(url=u) or (True, "Playing: X")
    )
    out = server._browser_play(
        browser, track="", artist="", url="https://music.apple.com/us/song/x/1"
    )
    assert calls["url"].endswith("/1")
    assert "Playing" in out


def test_browser_play_resolves_track(monkeypatch):
    import applemusic_mcp.browser as browser

    monkeypatch.setattr(
        server,
        "_resolve_catalog_track_itunes",
        lambda t, a="": {"name": t, "artist": a, "url": "https://music.apple.com/us/album/x/9?i=5"},
    )
    seen = {}
    monkeypatch.setattr(
        browser, "play_url", lambda u, s=False: seen.update(url=u) or (True, "Playing: T")
    )
    out = server._browser_play(browser, track="Strobe", artist="deadmau5")
    assert "i=5" in seen["url"]
    assert "Playing" in out


def test_browser_play_playlist_by_name(monkeypatch):
    """Browser playback must handle a playlist by name (parity with native)."""
    import applemusic_mcp.browser as browser

    monkeypatch.setattr(server, "_find_api_playlist_by_name", lambda n: ("p.rock", None))
    seen = {}
    monkeypatch.setattr(
        browser, "play_descriptor", lambda d, s=False: seen.update(d=d, s=s) or (True, "Playing")
    )
    out = server._browser_play(browser, playlist="Rock Classics")
    assert seen["d"] == {"playlist": "p.rock"}
    assert "Playing" in out


def test_browser_play_album_by_name(monkeypatch):
    import applemusic_mcp.browser as browser

    monkeypatch.setattr(
        server, "_find_matching_catalog_album", lambda a, ar="": ({"id": "alb1"}, None, None)
    )
    seen = {}
    monkeypatch.setattr(
        browser,
        "play_descriptor",
        lambda d, s=False: seen.update(d=d, s=s) or (True, "Playing"),
    )
    out = server._browser_play(browser, album="Abbey Road", shuffle=True)
    assert seen["d"] == {"album": "alb1"} and seen["s"] is True
    assert "Playing" in out


def test_browser_play_track_not_found(monkeypatch):
    monkeypatch.setattr(server, "_resolve_catalog_track_itunes", lambda t, a="": None)
    out = server._browser_play(None, track="zzznope", artist="")
    assert "not found" in out.lower()


def test_engine_is_always_api(monkeypatch):
    monkeypatch.setattr(server, "get_user_preferences", lambda: {"mode": "chrome"})
    assert server._engine() == "api"


def test_playback_tool_registered_unconditionally():
    """Regression: `playback` used to live inside `if APPLESCRIPT_AVAILABLE`, so
    it was missing entirely on Windows/Linux. It must be a module-level tool."""
    assert callable(getattr(server, "playback", None))


def test_playback_play_routes_to_browser(monkeypatch):
    monkeypatch.setattr(server, "_browser_play", lambda *a, **k: "Playing: X")
    assert server.playback(action="play", track="X") == "Playing: X"


def test_playback_reveal_in_browser(monkeypatch):
    """reveal opens the track's page in Chrome."""
    import applemusic_mcp.browser as browser

    monkeypatch.setattr(
        server,
        "_resolve_catalog_track_itunes",
        lambda n, a="": {"url": "https://music.apple.com/us/song/x/1"},
    )
    seen = {}
    monkeypatch.setattr(
        browser, "reveal_url", lambda u: seen.update(url=u) or (True, f"Showing in browser: {u}")
    )
    out = server.playback(action="reveal", track="X")
    assert "/song/x/1" in seen["url"]
    assert "browser" in out.lower()
