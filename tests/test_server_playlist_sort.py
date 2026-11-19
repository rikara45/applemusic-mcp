"""Playlist sort — unit tests, all mocked at the amp_api boundary."""

import json

import pytest

from applemusic_mcp import server


def _t(artist, name, rid, album="Album", disc=1, track=1):
    return {
        "relationship_id": rid,
        "name": name,
        "artist": artist,
        "album": album,
        "disc_number": disc,
        "track_number": track,
        "catalog_id": "",
    }


def test_sort_track_rows_orders_artist_album_track():
    tracks = [
        _t("LE SSERAFIM", "HOT 2", "i.1", "HOT", track=2),
        _t("LE SSERAFIM", "CRAZY 3", "i.2", "CRAZY", track=3),
        _t("LE SSERAFIM", "HOT 1", "i.3", "HOT", track=1),
        _t("LE SSERAFIM", "CRAZY 1", "i.4", "CRAZY", track=1),
    ]
    assert [t["relationship_id"] for t in server._sort_track_rows(tracks)] == [
        "i.4",
        "i.2",
        "i.3",
        "i.1",
    ]


def test_sort_track_rows_is_case_insensitive_and_keeps_ties():
    tracks = [
        _t("beatles", "B", "i.1", "abbey road", track=1),
        _t("Beatles", "A", "i.2", "Abbey Road", track=1),
        _t("ABBA", "M", "i.3", "Arrival", track=1),
    ]
    assert [t["relationship_id"] for t in server._sort_track_rows(tracks)] == ["i.3", "i.1", "i.2"]


def test_sort_track_rows_puts_missing_artist_and_album_last():
    tracks = [
        _t("", "No Artist", "i.1", ""),
        _t("ABBA", "No Album", "i.2", ""),
        _t("ABBA", "Arrival", "i.3", "Arrival", track=1),
    ]
    assert [t["relationship_id"] for t in server._sort_track_rows(tracks)] == ["i.3", "i.2", "i.1"]


def test_sort_track_rows_treats_missing_track_number_as_zero():
    tracks = [
        _t("A", "Second", "i.1", "Album", track=2),
        _t("A", "Unknown", "i.2", "Album", track=0),
    ]
    assert [t["relationship_id"] for t in server._sort_track_rows(tracks)] == ["i.2", "i.1"]


@pytest.fixture
def sort_env(monkeypatch, tmp_path):
    """Resolve to an API-created playlist and point backups at tmp_path."""
    monkeypatch.setattr(
        server,
        "_resolve_playlist",
        lambda name: server.ResolvedPlaylist(api_id="p.1", raw_input=name),
    )
    monkeypatch.setattr(
        server.amp_api,
        "list_playlists",
        lambda: [{"id": "p.1", "name": "PL", "canEdit": True, "globalId": ""}],
    )
    monkeypatch.setattr(server, "get_cache_dir", lambda: tmp_path)
    return tmp_path


UNSORTED = [
    _t("B", "b", "i.1", "Album B", track=1),
    _t("A", "z", "i.2", "Album A", track=2),
    _t("A", "a", "i.3", "Album A", track=1),
]
SORTED = [UNSORTED[2], UNSORTED[1], UNSORTED[0]]


def test_non_api_playlist_refused_before_writing(sort_env, monkeypatch):
    monkeypatch.setattr(
        server.amp_api,
        "list_playlists",
        lambda: [{"id": "p.1", "name": "PL", "canEdit": False, "globalId": "pl.u-abc"}],
    )
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: UNSORTED)
    monkeypatch.setattr(server.amp_api, "remove_track", lambda *a: pytest.fail("removed blind"))

    result = server._playlist_sort_api("PL")
    assert "can't be sorted" in result
    assert "created in Music.app" in result


def test_playlist_missing_from_listing(sort_env, monkeypatch):
    monkeypatch.setattr(server.amp_api, "list_playlists", lambda: [])
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: UNSORTED)
    result = server._playlist_sort_api("PL")
    assert "library listing" in result


def test_unreadable_playlist(sort_env, monkeypatch):
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: None)
    result = server._playlist_sort_api("PL")
    assert "Couldn't read" in result


def test_empty_playlist(sort_env, monkeypatch):
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: [])
    result = server._playlist_sort_api("PL")
    assert "empty" in result


def test_already_sorted_is_a_noop(sort_env, monkeypatch):
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: SORTED)
    monkeypatch.setattr(server.amp_api, "remove_track", lambda *a: pytest.fail("removed"))
    monkeypatch.setattr(server.amp_api, "add_tracks", lambda *a: pytest.fail("added"))

    result = server._playlist_sort_api("PL")
    assert "already sorted" in result
    assert not (sort_env / "snapshots").exists()


def test_dry_run_writes_nothing(sort_env, monkeypatch):
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: UNSORTED)
    monkeypatch.setattr(server.amp_api, "remove_track", lambda *a: pytest.fail("removed"))
    monkeypatch.setattr(server.amp_api, "add_tracks", lambda *a: pytest.fail("added"))

    result = server._playlist_sort_api("PL", dry_run=True)
    assert "Preview" in result
    assert "nothing written" in result
    assert "1. A — Album A — a" in result
    assert not (sort_env / "snapshots").exists()


def test_preview_caps_at_30_lines(sort_env, monkeypatch):
    tracks = [_t("Artist", f"Song {n:02d}", f"i.{n}", "Album", track=n) for n in range(34, -1, -1)]
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: tracks)

    result = server._playlist_sort_api("PL", dry_run=True)
    assert "… and 5 more" in result


def test_throttled_session_refuses_before_mutation(sort_env, monkeypatch):
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: UNSORTED)
    monkeypatch.setattr(server.amp_api, "throttled_recently", lambda: True)
    monkeypatch.setattr(server.amp_api, "remove_track", lambda *a: pytest.fail("removed"))
    monkeypatch.setattr(server.amp_api, "add_tracks", lambda *a: pytest.fail("added"))

    result = server._playlist_sort_api("PL")
    assert result == server._SESSION_THROTTLED_MSG
    assert not (sort_env / "snapshots").exists()


def test_write_sort_backup_records_original_order(sort_env):
    path = server._write_sort_backup("p.1", "PL", UNSORTED)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert path.parent == sort_env / "snapshots"
    assert [t["relationship_id"] for t in saved["tracks"]] == ["i.1", "i.2", "i.3"]
    assert saved["playlist"] == "PL"


def _wire(sort_env, monkeypatch, tracks, after):
    """Common amp_api stubs: reads queue then returns `after`."""
    reads = [tracks, after]

    def fake_get(pid):
        return reads.pop(0) if reads else after

    removed, added = [], []
    monkeypatch.setattr(server.amp_api, "get_tracks", fake_get)
    monkeypatch.setattr(
        server.amp_api,
        "remove_track",
        lambda pid, rid: (removed.append(rid) or True, "Removed"),
    )
    monkeypatch.setattr(
        server.amp_api,
        "add_tracks",
        lambda pid, items: (added.append(items) or True, "Added"),
    )
    return removed, added


def test_happy_path_removes_then_adds_sorted(sort_env, monkeypatch):
    tracks = [
        _t("B", "b", "i.1", "Album B", track=1),
        _t("A", "z", "i.2", "Album A", track=2),
        _t("A", "a", "i.3", "Album A", track=1),
    ]
    sorted_tracks = [tracks[2], tracks[1], tracks[0]]
    removed, added = _wire(sort_env, monkeypatch, tracks, sorted_tracks)
    monkeypatch.setattr(server.audit_log, "log_action", lambda *a, **k: None)

    result = server._playlist_sort_api("PL")
    assert "Sorted 3 tracks" in result
    assert removed == ["i.1", "i.2", "i.3"]
    assert [item[0] for item in added[0]] == ["i.3", "i.2", "i.1"]
    assert all(item[1] == "library-songs" for item in added[0])
    assert "1. A — Album A — a" in result


def test_large_playlist_adds_in_batches_of_25(sort_env, monkeypatch):
    tracks = [_t("Artist", f"Song {n:02d}", f"i.{n}", "Album", track=n) for n in range(29, -1, -1)]
    removed, added = _wire(sort_env, monkeypatch, tracks, list(reversed(tracks)))

    server._playlist_sort_api("PL")
    assert len(removed) == 30
    assert [len(batch) for batch in added] == [25, 5]


def test_duplicate_tracks_are_not_deduped(sort_env, monkeypatch):
    tracks = [
        _t("B", "x", "i.3", "Album B", track=1),
        _t("A", "Same", "i.1", "Album A", track=1),
        _t("A", "Same", "i.2", "Album A", track=1),
    ]
    removed, added = _wire(sort_env, monkeypatch, tracks, [tracks[1], tracks[2], tracks[0]])

    server._playlist_sort_api("PL")
    assert removed == ["i.3", "i.1", "i.2"]
    assert [item[0] for item in added[0]] == ["i.1", "i.2", "i.3"]


def test_verification_mismatch_warns(sort_env, monkeypatch):
    tracks = [_t("B", "b", "i.1", "Album B", track=1), _t("A", "a", "i.2", "Album A", track=1)]
    _wire(sort_env, monkeypatch, tracks, tracks)  # re-read still unsorted

    result = server._playlist_sort_api("PL")
    assert "⚠️" in result
    assert "Backup:" in result
    assert "Sorted 2 tracks" not in result


def test_audit_log_records_sort(sort_env, monkeypatch):
    tracks = [_t("B", "b", "i.1", "Album B", track=1), _t("A", "a", "i.2", "Album A", track=1)]
    _wire(sort_env, monkeypatch, tracks, [tracks[1], tracks[0]])
    logged = []
    monkeypatch.setattr(server.audit_log, "log_action", lambda *a, **k: logged.append((a, k)))

    server._playlist_sort_api("PL")
    assert logged[0][0][0] == "sort_playlist"
    assert logged[0][0][1]["tracks"] == 2


def test_remove_failure_restores_only_what_was_removed(sort_env, monkeypatch):
    tracks = [
        _t("B", "b", "i.1", "Album B", track=1),
        _t("A", "a", "i.2", "Album A", track=1),
        _t("A", "z", "i.3", "Album A", track=2),
    ]
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: tracks)  # rollback read
    calls = []

    def fake_remove(pid, rid):
        calls.append(rid)
        return (len(calls) == 1, "Removed" if len(calls) == 1 else "status 429")

    added = []
    monkeypatch.setattr(server.amp_api, "remove_track", fake_remove)
    monkeypatch.setattr(
        server.amp_api, "add_tracks", lambda pid, items: (added.append(items) or True, "Added")
    )

    result = server._playlist_sort_api("PL")
    assert "couldn't finish" in result
    assert "Restored 1 track" in result
    assert [item[0] for item in added[0]] == ["i.1"]


def test_first_remove_failure_reports_unchanged(sort_env, monkeypatch):
    tracks = [
        _t("B", "b", "i.1", "Album B", track=1),
        _t("A", "a", "i.2", "Album A", track=1),
    ]
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: tracks)
    monkeypatch.setattr(server.amp_api, "remove_track", lambda pid, rid: (False, "status 429"))
    monkeypatch.setattr(server.amp_api, "add_tracks", lambda *a: pytest.fail("added"))

    result = server._playlist_sort_api("PL")
    assert "playlist was unchanged" in result


def test_add_failure_rolls_back_new_entries_then_restores(sort_env, monkeypatch):
    tracks = [
        _t("B", "b", "i.1", "Album B", track=1),
        _t("A", "a", "i.2", "Album A", track=1),
    ]
    # Reads: initial, rollback current (one new partial entry).
    reads = [tracks, [_t("A", "a", "i.NEW", "Album A", track=1)]]

    def fake_get(pid):
        return reads.pop(0) if reads else []

    removed, add_calls = [], []
    monkeypatch.setattr(server.amp_api, "get_tracks", fake_get)
    monkeypatch.setattr(
        server.amp_api, "remove_track", lambda pid, rid: (removed.append(rid) or True, "Removed")
    )

    def fake_add(pid, items):
        add_calls.append([i[0] for i in items])
        return (len(add_calls) > 1, "status 500" if len(add_calls) == 1 else "Added")

    monkeypatch.setattr(server.amp_api, "add_tracks", fake_add)

    result = server._playlist_sort_api("PL")
    assert "couldn't finish" in result
    assert "i.NEW" in removed  # new partial entry cleaned up
    assert add_calls[-1] == ["i.1", "i.2"]  # originals restored in order
    assert "Restored 2 track(s)" in result


def test_failed_restore_names_the_backup(sort_env, monkeypatch):
    tracks = [
        _t("B", "b", "i.1", "Album B", track=1),
        _t("A", "a", "i.2", "Album A", track=1),
    ]
    monkeypatch.setattr(server.amp_api, "get_tracks", lambda pid: tracks)
    calls = []

    def fake_remove(pid, rid):
        calls.append(rid)
        return (len(calls) == 1, "Removed" if len(calls) == 1 else "status 429")

    monkeypatch.setattr(server.amp_api, "remove_track", fake_remove)
    monkeypatch.setattr(server.amp_api, "add_tracks", lambda pid, items: (False, "status 429"))

    result = server._playlist_sort_api("PL")
    assert "Restore failed" in result
    assert "may be missing tracks" in result
    assert "Backup:" in result


def test_unreadable_playlist_during_cleanup_still_restores(sort_env, monkeypatch):
    tracks = [_t("B", "b", "i.1", "Album B"), _t("A", "a", "i.2", "Album A")]
    reads = [tracks, None]  # initial read, then rollback read fails

    def fake_get(pid):
        return reads.pop(0) if reads else None

    added = []
    monkeypatch.setattr(server.amp_api, "get_tracks", fake_get)
    monkeypatch.setattr(
        server.amp_api, "remove_track", lambda pid, rid: (rid == "i.1", "status 429")
    )
    monkeypatch.setattr(
        server.amp_api, "add_tracks", lambda pid, items: (added.append(items) or True, "Added")
    )

    result = server._playlist_sort_api("PL")
    assert "Couldn't read the playlist to clean up partial adds" in result
    assert [item[0] for item in added[0]] == ["i.1"]


def test_failed_partial_add_cleanup_is_reported(sort_env, monkeypatch):
    tracks = [_t("B", "b", "i.1", "Album B"), _t("A", "a", "i.2", "Album A")]
    reads = [tracks, [_t("A", "a", "i.NEW", "Album A")]]
    added = []

    def fake_get(pid):
        return reads.pop(0) if reads else []

    def fake_remove(pid, rid):
        return (rid != "i.NEW", "status 500" if rid == "i.NEW" else "Removed")

    def fake_add(pid, items):
        added.append([i[0] for i in items])
        return (len(added) > 1, "status 500" if len(added) == 1 else "Added")

    monkeypatch.setattr(server.amp_api, "get_tracks", fake_get)
    monkeypatch.setattr(server.amp_api, "remove_track", fake_remove)
    monkeypatch.setattr(server.amp_api, "add_tracks", fake_add)

    result = server._playlist_sort_api("PL")
    assert "Couldn't remove a partial add (status 500)" in result
    assert "Restored 2 track(s)" in result
    assert added[-1] == ["i.1", "i.2"]


def test_playlist_action_routes_sort(monkeypatch):
    monkeypatch.setattr(server, "_playlist_sort_api", lambda p, dry: "Sorted 2 tracks")
    assert server.playlist(action="sort", playlist="PL") == "Sorted 2 tracks (via web player)"


def test_playlist_action_passes_dry_run(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        server, "_playlist_sort_api", lambda p, dry: (seen.update(playlist=p, dry=dry) or "Preview")
    )
    server.playlist(action="sort", playlist="PL", dry_run=True)
    assert seen == {"playlist": "PL", "dry": True}


def test_playlist_unknown_action_lists_sort():
    result = server.playlist(action="nope")
    assert "sort" in result


@pytest.mark.parametrize(
    "message",
    [
        "Preview: 'PL' would be reordered (2 tracks) — nothing written.\n  1. A — Album — a",
        "'PL' is empty — nothing to sort.",
        "'PL' is already sorted by artist and album.",
    ],
)
def test_playlist_action_labels_only_writes(monkeypatch, message):
    monkeypatch.setattr(server, "_playlist_sort_api", lambda p, dry: message)
    assert server.playlist(action="sort", playlist="PL") == message
