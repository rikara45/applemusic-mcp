"""Engine resolution: stored `mode` × per-call override × permission level → engine.

Playback and Up Next run only in the Chrome web player in this build, so every
mode resolves to ``chrome`` (or ``none`` below the ``web`` level) and the data
engine is always the API. The native (Music.app) and Safari players are gone.
"""

from __future__ import annotations

import pytest

import applemusic_mcp.server as server


@pytest.fixture
def mode(monkeypatch):
    """Set the `mode` pref (and optionally the permission level); return a setter."""

    def _set(mode_value, level=None):
        monkeypatch.setattr(server, "get_user_preferences", lambda: {"mode": mode_value})
        if level is not None:
            monkeypatch.setenv("APPLEMUSIC_LEVEL", level)

    return _set


# -- plain playback ---------------------------------------------------------


@pytest.mark.parametrize(
    "mode_value", ["auto", "native", "safari", "chrome", "web", "browser", "api"]
)
def test_playback_engine_is_chrome(mode, mode_value):
    mode(mode_value)
    assert server._playback_engine() == "chrome"


@pytest.mark.parametrize("override", ["chrome", "web", "browser", "auto"])
def test_playback_engine_override(mode, override):
    mode("auto")
    assert server._playback_engine(engine_override=override) == "chrome"


# -- queue (Up Next) --------------------------------------------------------


@pytest.mark.parametrize(
    "mode_value", ["auto", "native", "safari", "chrome", "web", "browser", "api"]
)
def test_queue_engine_is_chrome(mode, mode_value):
    mode(mode_value)
    assert server._queue_engine() == "chrome"


def test_queue_engine_override(mode):
    mode("auto")
    assert server._queue_engine(engine_override="chrome") == "chrome"


# -- data engine ------------------------------------------------------------


@pytest.mark.parametrize("mode_value", ["auto", "native", "safari", "chrome", "web", "api"])
def test_data_engine_is_api(mode, mode_value):
    mode(mode_value)
    assert server._engine() == "api"


# -- module selection + error messaging -------------------------------------


def test_web_player_is_browser():
    from applemusic_mcp import browser

    assert server._web_player("chrome") is browser


def test_web_player_refused_below_web(monkeypatch):
    from applemusic_mcp.capabilities import CapabilityDenied

    monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
    with pytest.raises(CapabilityDenied):
        server._web_player("chrome")


def test_no_player_msg_names_chrome():
    msg = server._no_player_msg()
    assert "Chrome" in msg and "login" in msg


def test_no_player_msg_for_queue_below_web(monkeypatch):
    monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
    msg = server._no_player_msg(for_queue=True)
    assert "Up Next" in msg and "--level web" in msg and "Chrome" not in msg


# -- permission levels ------------------------------------------------------


@pytest.mark.parametrize("stored", ["auto", "native", "safari", "chrome", "web"])
@pytest.mark.parametrize("level", ["api"])
def test_engine_below_web_level_is_none(mode, stored, level):
    mode(stored, level)
    assert server._playback_engine() == "none"
    assert server._queue_engine() == "none"


@pytest.mark.parametrize("stored", ["auto", "native", "safari", "chrome", "web"])
def test_engine_still_chrome_at_web_level(mode, stored):
    mode(stored, "web")
    assert server._playback_engine() == "chrome"
    assert server._queue_engine() == "chrome"


@pytest.mark.parametrize("override", ["chrome", "safari"])
def test_engine_override_cannot_exceed_level(mode, monkeypatch, override):
    mode("auto", "api")
    assert server._playback_engine(override) == "none"


@pytest.mark.parametrize(
    "level,hidden",
    [("api", {"queue"}), ("web", set()), (None, set())],
)
def test_queue_tool_hidden_below_web(monkeypatch, level, hidden):
    from applemusic_mcp import capabilities

    if level is not None:
        monkeypatch.setenv("APPLEMUSIC_LEVEL", level)
    assert server.hidden_tools(capabilities.current()) == hidden


def test_conversational_signin_refused_below_web(monkeypatch):
    monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
    out = server.config(action="signin")
    assert "--level web" in out and "applemusic-mcp login" in out
