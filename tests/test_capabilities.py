"""Permission levels: parsing, the env contract, and refusal messages."""

import pytest

from applemusic_mcp import capabilities as caps


def test_unset_is_unrestricted():
    lvl = caps.parse(None)
    assert (lvl.name, lvl.web, lvl.explicit) == ("unrestricted", True, False)


@pytest.mark.parametrize(
    "raw,web",
    [
        ("api", False),
        ("web", True),
        (" API ", False),
        ("Web", True),
    ],
)
def test_levels_normalize(raw, web):
    lvl = caps.parse(raw)
    assert (lvl.web, lvl.explicit) == (web, True)


@pytest.mark.parametrize("raw", ["apis", "all", "", "native", "music"])
def test_bad_level_raises(raw):
    with pytest.raises(ValueError, match="api, web, full"):
        caps.parse(raw)


def test_current_reads_env(monkeypatch):
    monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
    lvl = caps.current()
    assert (lvl.name, lvl.web) == ("api", False)


def test_require_denies_with_client_config_hint(monkeypatch):
    monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
    with pytest.raises(caps.CapabilityDenied) as e:
        caps.require("web", "Chrome playback")
    msg = str(e.value)
    assert "Chrome playback" in msg and "--level web" in msg and "(api)" in msg
    assert "client config" in msg


def test_require_passes_when_allowed(monkeypatch):
    monkeypatch.setenv("APPLEMUSIC_LEVEL", "web")
    caps.require("web", "x")


def test_require_passes_unrestricted():
    caps.require("web", "x")


def test_modes_by_level():
    assert caps.MODES_BY_LEVEL["api"] == ("auto", "api")
    assert "chrome" not in caps.MODES_BY_LEVEL["api"]
    assert {"chrome", "web"} <= set(caps.MODES_BY_LEVEL["web"])


# -- tool surface ------------------------------------------------------------

import asyncio  # noqa: E402

import applemusic_mcp.server as server  # noqa: E402

FastMCP = server.FastMCP  # mcp 1.x FastMCP or 2.x MCPServer, per server's import shim


@pytest.mark.parametrize(
    "level,hidden",
    [
        ("api", {"queue"}),
        ("web", set()),
        (None, set()),
    ],
)
def test_hidden_tools(level, hidden):
    assert server.hidden_tools(caps.parse(level)) == hidden


def test_apply_level_removes_tools(monkeypatch):
    app = FastMCP("t")
    for name in ("playlist", "catalog", "discover", "queue", "vibe"):
        app.tool(name=name)(lambda: "")
    monkeypatch.setenv("APPLEMUSIC_LEVEL", "api")
    removed = server.apply_level(app)
    names = {t.name for t in asyncio.run(app.list_tools())}
    assert set(removed) == {"queue"} and "queue" not in names
    assert {"playlist", "catalog", "discover", "vibe"} <= names


def test_apply_level_unrestricted_keeps_all():
    app = FastMCP("t")
    for name in ("playlist", "queue"):
        app.tool(name=name)(lambda: "")
    assert server.apply_level(app) == []
    assert {t.name for t in asyncio.run(app.list_tools())} == {"playlist", "queue"}


def test_server_registers_the_tools_hidden_tools_names():
    # hidden_tools names must match real registrations or hiding silently fails.
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"catalog", "discover", "queue", "vibe"} <= names


# -- full: everything, named --------------------------------------------------


def test_full_level_allows_everything():
    lvl = caps.parse("full")
    assert (lvl.name, lvl.web, lvl.explicit) == ("full", True, True)


def test_full_level_allows_every_mode():
    assert set(caps.MODES_BY_LEVEL["full"]) == {"auto", "api", "chrome", "web"}


def test_full_level_hides_no_tools():
    assert server.hidden_tools(caps.parse("full")) == set()


def test_full_level_requires_nothing(monkeypatch):
    monkeypatch.setenv("APPLEMUSIC_LEVEL", "full")
    caps.require("web", "x")
