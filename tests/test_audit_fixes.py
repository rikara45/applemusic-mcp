"""Regression tests for the pre-1.0 release-audit findings."""

from __future__ import annotations

import applemusic_mcp.server as server

# -- HIGH: destructive web ops must not destroy the wrong playlist ----------


def test_playlist_delete_ambiguous_refuses(monkeypatch):
    monkeypatch.setattr(
        server.amp_api,
        "list_playlists",
        lambda: [{"id": "p.1", "name": "Jazz Favorites"}, {"id": "p.2", "name": "Jazz Vibes"}],
    )
    monkeypatch.setattr(server.amp_api, "delete_playlist", lambda pid: (True, ""))
    out = server._playlist_delete_api("Jazz")  # no exact match → 2 substring matches
    assert out.startswith("Error") and "multiple" in out.lower()
    assert "Jazz Favorites" in out and "Jazz Vibes" in out


def test_playlist_delete_echoes_resolved_name(monkeypatch):
    deleted = {}
    monkeypatch.setattr(
        server.amp_api, "list_playlists", lambda: [{"id": "p.1", "name": "Jazz Favorites"}]
    )
    monkeypatch.setattr(
        server.amp_api, "delete_playlist", lambda pid: deleted.update(pid=pid) or (True, "")
    )
    out = server._playlist_delete_api("Jazz")  # single substring match
    assert deleted["pid"] == "p.1"
    assert "Deleted playlist: Jazz Favorites" in out  # the RESOLVED name, not "Jazz"


def test_playlist_delete_exact_wins(monkeypatch):
    monkeypatch.setattr(
        server.amp_api,
        "list_playlists",
        lambda: [{"id": "p.1", "name": "Jazz"}, {"id": "p.2", "name": "Jazz Favorites"}],
    )
    monkeypatch.setattr(server.amp_api, "delete_playlist", lambda pid: (True, ""))
    out = server._playlist_delete_api("Jazz")
    assert "Deleted playlist: Jazz" in out and "Favorites" not in out


def test_playlist_rename_ambiguous_refuses(monkeypatch):
    monkeypatch.setattr(
        server.amp_api,
        "list_playlists",
        lambda: [{"id": "p.1", "name": "Workout A"}, {"id": "p.2", "name": "Workout B"}],
    )
    monkeypatch.setattr(server.amp_api, "rename_playlist", lambda pid, n: (True, ""))
    out = server._playlist_rename_api("Workout", "New")
    assert out.startswith("Error") and "multiple" in out.lower()


# -- MEDIUM: off-macOS playlist add must de-dup + honor auto_add -------------

import types  # noqa: E402


def _ri(input_type, value, artist="", error=None):
    return types.SimpleNamespace(input_type=input_type, value=value, artist=artist, error=error)


def test_playlist_add_api_dedup(monkeypatch):
    monkeypatch.setattr(server, "_find_api_playlist_by_name", lambda n: ("p.1", None))
    monkeypatch.setattr(
        server.amp_api,
        "get_tracks",
        lambda pid: [{"catalog_id": "123", "name": "Africa", "artist": "Toto"}],
    )
    monkeypatch.setattr(
        server, "_resolve_track", lambda t, a="": [_ri(server.InputType.CATALOG_ID, "123")]
    )
    calls = {}
    monkeypatch.setattr(
        server.amp_api, "add_tracks", lambda pid, items: calls.update(items=items) or (True, "")
    )
    out = server._playlist_add_api("Mix", "123")  # already present
    assert "items" not in calls  # add_tracks NOT called — de-duped
    assert "already in the playlist" in out.lower()


def test_playlist_add_api_auto_add_off_skips_catalog(monkeypatch):
    monkeypatch.setattr(server, "_find_api_playlist_by_name", lambda n: ("p.1", None))
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: [])
    monkeypatch.setattr(
        server, "_resolve_track", lambda t, a="": [_ri(server.InputType.NAME, "New Song")]
    )
    monkeypatch.setattr(server.amp_api, "search_library_songs", lambda q, n=1: [])  # not in library
    searched = {}
    monkeypatch.setattr(
        server.amp_api,
        "search_catalog_songs",
        lambda q, n=1: searched.update(hit=1) or [{"id": "999", "name": "New Song", "artist": "X"}],
    )
    monkeypatch.setattr(server.amp_api, "add_tracks", lambda pid, items: (True, ""))
    out = server._playlist_add_api("Mix", "New Song", auto_add=False)
    assert "hit" not in searched  # did NOT catalog-search when opted out
    assert "auto_add=True" in out


def test_playlist_add_api_auto_add_on_searches_catalog(monkeypatch):
    monkeypatch.setattr(server, "_find_api_playlist_by_name", lambda n: ("p.1", None))
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: [])
    monkeypatch.setattr(
        server, "_resolve_track", lambda t, a="": [_ri(server.InputType.NAME, "New Song")]
    )
    monkeypatch.setattr(
        server.amp_api,
        "search_catalog_songs",
        lambda q, n=1: [{"id": "999", "name": "New Song", "artist": "X"}],
    )
    added = {}
    monkeypatch.setattr(
        server.amp_api, "add_tracks", lambda pid, items: added.update(items=items) or (True, "")
    )
    out = server._playlist_add_api("Mix", "New Song", auto_add=True)
    assert added["items"] == ["999"] and "New Song" in out


# -- dev-session: transactional swap never loses the old track --------------


# -- off-mac (Win/Linux) swap: API read-back guards the destructive remove --------


def _fake_resolved(api_id="p.1", name="Jazz"):
    import types as _t

    return _t.SimpleNamespace(api_id=api_id, applescript_name=name, error=None, fuzzy_match=None)


def test_offmac_swap_aborts_when_confirm_fails(monkeypatch):
    """Off-mac there's no native verify; if the strict confirm can't find the exact
    track, the old track is NOT removed."""
    monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
    monkeypatch.setattr(server, "_playlist_add_api", lambda *a, **k: "Added Coltrane to Jazz")
    monkeypatch.setattr(server, "_resolve_playlist", lambda p: _fake_resolved())
    monkeypatch.setattr(server, "_confirm_swap_track", lambda *a, **k: False)
    removed = []
    monkeypatch.setattr(server, "_playlist_remove_api", lambda p, t, ar: removed.append(t) or "rm")
    out = server.playlist(action="add", playlist="Jazz", track="Coltrane", replace="Old Song")
    assert "aborted" in out.lower() and removed == []


def test_offmac_swap_removes_old_after_confirm(monkeypatch):
    monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
    monkeypatch.setattr(server, "_playlist_add_api", lambda *a, **k: "Added Coltrane to Jazz")
    monkeypatch.setattr(server, "_resolve_playlist", lambda p: _fake_resolved())
    monkeypatch.setattr(server, "_confirm_swap_track", lambda *a, **k: True)
    removed = []
    monkeypatch.setattr(server, "_playlist_remove_api", lambda p, t, ar: removed.append(t) or "rm")
    out = server.playlist(action="add", playlist="Jazz", track="Coltrane", replace="Old Song")
    assert "Swapped" in out and removed == ["Old Song"]


def test_confirm_swap_track_requires_exact_name(monkeypatch):
    """The strict confirm must NOT accept a substring collision — adding 'One' while
    'One More Time' is present must return False (the data-loss fix)."""
    monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
    listing = (True, [{"name": "One More Time", "artist": "Daft Punk"}])
    monkeypatch.setattr(server, "_get_playlist_track_names", lambda pid: listing)
    assert server._confirm_swap_track("One", "", api_id="p.1") is False
    exact = (True, [{"name": "One", "artist": "Metallica"}])
    monkeypatch.setattr(server, "_get_playlist_track_names", lambda pid: exact)
    assert server._confirm_swap_track("One", "", api_id="p.1") is True


# -- config dir: clean error instead of a raw traceback when unwritable -----------


def test_get_config_dir_raises_clean_error(monkeypatch):
    """An unwritable APPLEMUSIC_MCP_HOME (or root-owned mount) should give an
    actionable message, not a raw OSError traceback."""
    import pytest

    import applemusic_mcp.auth as auth

    def boom(*a, **k):
        raise PermissionError("Permission denied")

    monkeypatch.setattr("pathlib.Path.mkdir", boom)
    with pytest.raises(RuntimeError, match="config directory"):
        auth.get_config_dir()


def test_env_var_user_token_injection(monkeypatch):
    """APPLEMUSIC_USER_TOKEN is the headless/container injection path (for an Apple ID
    that requires a hardware key that can't reach the box)."""
    import applemusic_mcp.auth as auth

    monkeypatch.setenv("APPLEMUSIC_USER_TOKEN", "INJECTED-TOK")
    assert auth.get_user_token() == "INJECTED-TOK"
    assert auth.has_user_token() is True
