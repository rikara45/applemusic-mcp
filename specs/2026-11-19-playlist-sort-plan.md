# Playlist Sort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `playlist(action="sort")` mengurutkan isi playlist yang ada di tempat berdasarkan artis A–Z, lalu album A–Z, lalu nomor track.

**Architecture:** Apple tidak punya endpoint reorder, jadi isi playlist dibangun ulang: baca semua track, simpan backup, hapus semua relationship id, tambahkan ulang sesuai urutan target, lalu baca ulang untuk verifikasi. `amp_api.get_tracks` ditambah album/nomor track; sisanya di `server.py`.

**Tech Stack:** Python 3.10+, `requests` (sudah ada), pytest + `responses` untuk test. Tanpa dependency baru.

**Spec:** `specs/2026-11-19-playlist-sort-design.md`

## Global Constraints

- Hanya playlist dengan `amp_api.playlist_kind(pl) == "api"` yang boleh ditulis. Playlist Music.app/Apple-curated ditolak sebelum ada mutasi.
- Batch tambah maksimal 25 item; hapus tetap per relationship id (`amp_api.remove_track`).
- `dry_run=True` tidak menulis apa pun: tidak ada `remove_track`, `add_tracks`, atau file backup.
- Backup ditulis ke `get_cache_dir() / "snapshots"` (bukan root cache — `_gc_exports` boleh menghapus file di root).
- Urutan target: artis kosong paling akhir; album kosong paling akhir di dalam grup artis; `casefold()` + `strip()`; seri penuh mempertahankan urutan lama (stable).
- Output tool berbahasa Inggris, mengikuti isi `server.py` yang sudah ada.
- Jangan commit/push tanpa permintaan user (aturan global repo). Task berakhir di test hijau, bukan commit.
- Jalankan `ruff check .` dan `black .` sebelum menyatakan selesai.

## Review Focus

- Playlist berubah di client lain antara baca dan tulis: relationship id lama bisa gagal dihapus → harus berhenti, restore yang sudah terhapus, sebut path backup.
- Apple menerima 2xx tapi tidak menyimpan semua track: verifikasi baca ulang harus memberi peringatan, bukan klaim sukses.
- Dua lagu dengan artis+album+nomor track sama: keduanya harus tetap ada; hapus memakai `relationship_id`, bukan nama.
- Track tanpa artis/album/nomor track: tidak boleh crash; artis dan album kosong di akhir; nomor kosong dianggap 0.
- Rate limit/error di tengah proses: tidak boleh meninggalkan playlist kosong tanpa laporan dan backup.
- `dry_run`: tidak boleh ada file backup atau panggilan tulis.

---

### Task 1: Expose album and track numbers from the playlist read

**Files:**
- Modify: `src/applemusic_mcp/amp_api.py:258-291` (`get_tracks`)
- Test: `tests/test_amp_api.py:427-468`

**Interfaces:**
- Produces: `amp_api.get_tracks(playlist_id) -> list[dict] | None`, tiap dict berisi `relationship_id, name, artist, album, disc_number, track_number, catalog_id`.

- [ ] **Step 1: Update the failing test**

Di `tests/test_amp_api.py`, tambahkan atribut album/nomor ke dua track mock dan perbarui expected dict:

```python
    responses.add(
        responses.GET,
        f"{amp_api.AMP}/me/library/playlists/p.1/tracks",
        match=[responses.matchers.query_param_matcher({"limit": "100"})],
        json={
            "data": [
                {
                    "id": "i.REL1",
                    "attributes": {
                        "name": "Song1",
                        "artistName": "Artist1",
                        "albumName": "Album1",
                        "discNumber": 1,
                        "trackNumber": 2,
                        "playParams": {"catalogId": "c1"},
                    },
                }
            ],
            "next": "/v1/me/library/playlists/p.1/tracks?offset=100",
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{amp_api.AMP}/me/library/playlists/p.1/tracks",
        match=[responses.matchers.query_param_matcher({"offset": "100"})],
        json={
            "data": [
                {
                    "id": "i.REL2",
                    "attributes": {"name": "Song2", "playParams": {"id": "c2"}},
                }
            ]
        },
        status=200,
    )
    out = amp_api.get_tracks("p.1")
    assert out == [
        {
            "relationship_id": "i.REL1",
            "name": "Song1",
            "artist": "Artist1",
            "album": "Album1",
            "disc_number": 1,
            "track_number": 2,
            "catalog_id": "c1",
        },
        {
            "relationship_id": "i.REL2",
            "name": "Song2",
            "artist": "",
            "album": "",
            "disc_number": 0,
            "track_number": 0,
            "catalog_id": "c2",
        },
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_amp_api.py::test_get_tracks_paginates_and_shapes -q`
Expected: FAIL — dict hasil belum punya `album`/`disc_number`/`track_number`.

- [ ] **Step 3: Implement the extra fields**

Di `amp_api.get_tracks`, ganti `out.append({...})` menjadi:

```python
                out.append(
                    {
                        "relationship_id": t.get("id"),  # i.xxx — needed for remove
                        "name": a.get("name", ""),
                        "artist": a.get("artistName", ""),
                        "album": a.get("albumName", ""),
                        "disc_number": a.get("discNumber") or 0,
                        "track_number": a.get("trackNumber") or 0,
                        "catalog_id": pp.get("catalogId") or pp.get("id"),
                    }
                )
```

Perbarui docstring fungsi menjadi:
`"Tracks in a playlist: [{relationship_id, name, artist, album, disc_number, track_number, catalog_id}]."`

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_amp_api.py -q`
Expected: pass.

---

### Task 2: Sort key helper

**Files:**
- Modify: `src/applemusic_mcp/server.py` (sisipkan sebelum `def _playlist_copy` di sekitar baris 3583)
- Create: `tests/test_server_playlist_sort.py`

**Interfaces:**
- Produces: `_sort_track_rows(tracks: list[dict]) -> list[dict]` — list baru berisi dict yang sama, urut `(artist, album, disc_number, track_number)`.

- [ ] **Step 1: Write the failing tests**

```python
"""Playlist sort — unit tests, all mocked at the amp_api boundary."""

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_playlist_sort.py -q`
Expected: FAIL — `_sort_track_rows` belum ada.

- [ ] **Step 3: Implement `_sort_track_rows`**

```python
def _sort_track_rows(tracks: list[dict]) -> list[dict]:
    """Artist A–Z, album A–Z, then disc/track; ties keep their original order."""
    return [
        t
        for _, t in sorted(
            enumerate(tracks),
            key=lambda it: (
                1 if not it[1].get("artist") else 0,
                (it[1].get("artist") or "").casefold().strip(),
                1 if not it[1].get("album") else 0,
                (it[1].get("album") or "").casefold().strip(),
                it[1].get("disc_number") or 0,
                it[1].get("track_number") or 0,
                it[0],
            ),
        )
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server_playlist_sort.py -q`
Expected: 4 passed.

---

### Task 3: Read, validate, no-op, dry-run, backup

**Files:**
- Modify: `src/applemusic_mcp/server.py` (helper baru + `_playlist_sort_api` sebelum `_playlist_copy`)
- Test: `tests/test_server_playlist_sort.py`

**Interfaces:**
- Consumes: `_sort_track_rows`, `_resolve_playlist`, `_unreadable_playlist_msg`, `amp_api.list_playlists`, `amp_api.playlist_kind`, `amp_api.get_tracks`, `get_cache_dir`, `get_timestamp`.
- Produces:
  - `_format_sort_preview(rows: list[dict], limit: int = _SORT_PREVIEW_LIMIT) -> str`
  - `_write_sort_backup(playlist_id: str, playlist_name: str, tracks: list[dict]) -> Path`
  - `_playlist_sort_api(playlist: str, dry_run: bool = False) -> str` (berhenti sebelum mutasi)
  - konstanta `_SORT_BATCH = 25`, `_SORT_PREVIEW_LIMIT = 30`

- [ ] **Step 1: Write the failing tests**

Tambahkan ke `tests/test_server_playlist_sort.py`:

```python
import json

import pytest


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


def test_write_sort_backup_records_original_order(sort_env):
    path = server._write_sort_backup("p.1", "PL", UNSORTED)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert path.parent == sort_env / "snapshots"
    assert [t["relationship_id"] for t in saved["tracks"]] == ["i.1", "i.2", "i.3"]
    assert saved["playlist"] == "PL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_playlist_sort.py -q`
Expected: FAIL — `_playlist_sort_api` / `_write_sort_backup` belum ada.

- [ ] **Step 3: Implement read/validate/no-op/dry-run/backup**

Letakkan konstanta dan helper sebelum `_playlist_copy`:

```python
_SORT_BATCH = 25
_SORT_PREVIEW_LIMIT = 30


def _format_sort_preview(rows: list[dict], limit: int = _SORT_PREVIEW_LIMIT) -> str:
    """Numbered artist — album — title lines, capped so a huge playlist stays readable."""
    lines = [
        f"  {i}. {t.get('artist') or 'Unknown artist'} — "
        f"{t.get('album') or 'Unknown album'} — {t.get('name') or 'Unknown title'}"
        for i, t in enumerate(rows[:limit], 1)
    ]
    if len(rows) > limit:
        lines.append(f"  … and {len(rows) - limit} more (playlist(action='tracks') to see all)")
    return "\n".join(lines)


def _write_sort_backup(playlist_id: str, playlist_name: str, tracks: list[dict]) -> Path:
    """Snapshot the original order before a rewrite; never touches the export GC root."""
    backups = get_cache_dir() / "snapshots"
    backups.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", playlist_id)
    path = backups / f"{safe_id}_sort_{get_timestamp()}.json"
    path.write_text(
        json.dumps(
            {
                "playlist": playlist_name,
                "playlist_id": playlist_id,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "tracks": tracks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
```

Lalu `_playlist_sort_api` (baca/validasi/no-op/dry-run dulu; mutasi di Task 4):

```python
def _playlist_sort_api(playlist: str, dry_run: bool = False) -> str:
    """Reorder a playlist in place: artist A–Z, then album A–Z and track number."""
    resolved = _resolve_playlist(playlist)
    if resolved.error:
        return resolved.error
    pid = resolved.api_id
    if not pid:
        return _resolve_failure_msg(f"playlist {playlist!r} not found in your library")

    pl = next((p for p in amp_api.list_playlists() if p.get("id") == pid), None)
    if pl is None:
        return (
            f"Error: couldn't find '{playlist}' in your library listing. Try again in a moment."
        )
    if amp_api.playlist_kind(pl) != "api":
        return (
            f"Error: '{playlist}' can't be sorted over the Apple Music web API — it was "
            "created in Music.app (or is Apple-curated). Only playlists created through "
            "this tool can be rewritten."
        )

    tracks = amp_api.get_tracks(pid)
    if tracks is None:
        return _unreadable_playlist_msg(playlist)
    if not tracks:
        return f"'{playlist}' is empty — nothing to sort."

    target = _sort_track_rows(tracks)
    if [t["relationship_id"] for t in target] == [t["relationship_id"] for t in tracks]:
        return f"'{playlist}' is already sorted by artist and album."

    if dry_run:
        return (
            f"Preview: '{playlist}' would be reordered ({len(target)} tracks) — "
            "nothing written.\n" + _format_sort_preview(target)
        )

    # Mutasi (remove/add/verify) ditambahkan di Task 4.
    return _write_sort_backup(pid, pl["name"], tracks).as_posix()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server_playlist_sort.py -q`
Expected: semua test Task 2 + Task 3 pass.

---

### Task 4: Rewrite path — remove, add, verify, audit

**Files:**
- Modify: `src/applemusic_mcp/server.py` (`_playlist_sort_api` menggantikan placeholder `return _write_sort_backup(...)`)
- Test: `tests/test_server_playlist_sort.py`

**Interfaces:**
- Consumes: `_write_sort_backup`, `_format_sort_preview`, `amp_api.remove_track`, `amp_api.add_tracks`, `amp_api.get_tracks`, `audit_log.log_action`.
- Produces: alur sukses `"Sorted N tracks in 'X' by artist and album.\n<preview>"`; tidak ada helper baru.

- [ ] **Step 1: Write the failing tests**

```python
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
    tracks = [
        _t("Artist", f"Song {n:02d}", f"i.{n}", "Album", track=n) for n in range(29, -1, -1)
    ]
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_playlist_sort.py -q`
Expected: FAIL — path backup dikembalikan, `Sorted` tidak muncul.

- [ ] **Step 3: Implement the rewrite**

Ganti placeholder di `_playlist_sort_api` dengan:

```python
    backup = _write_sort_backup(pid, pl["name"], tracks)
    removed: list[dict] = []
    for t in tracks:
        ok, msg = amp_api.remove_track(pid, t["relationship_id"])
        if not ok:
            return _rollback_sort(pid, tracks, removed, backup, f"couldn't remove a track ({msg})")
        removed.append(t)

    for i in range(0, len(target), _SORT_BATCH):
        batch = [(t["relationship_id"], "library-songs") for t in target[i : i + _SORT_BATCH]]
        ok, msg = amp_api.add_tracks(pid, batch)
        if not ok:
            return _rollback_sort(
                pid, tracks, removed, backup, f"couldn't add the sorted tracks ({msg})"
            )

    after = amp_api.get_tracks(pid)

    def _key(t: dict) -> tuple[str, str, str]:
        return (
            (t.get("artist") or "").casefold().strip(),
            (t.get("album") or "").casefold().strip(),
            (t.get("name") or "").casefold().strip(),
        )

    expected = [_key(t) for t in target]
    actual = [_key(t) for t in after] if after is not None else None

    # The rewrite happened (all removes and adds returned success), so log it even
    # when the re-read disagrees with the target.
    audit_log.log_action(
        "sort_playlist",
        {"playlist": pl["name"], "playlist_id": pid, "tracks": len(target), "via": "api"},
    )

    if actual is None:
        warning = f"\n\n⚠️ Couldn't confirm the new order: re-reading the playlist failed. Backup: {backup}"
    elif actual != expected:
        pos = next(
            (i for i, (a, b) in enumerate(zip(actual, expected), 1) if a != b),
            min(len(actual), len(expected)) + 1,
        )
        warning = (
            f"\n\n⚠️ The playlist was rewritten, but the re-read doesn't match the target "
            f"order (first difference at position {pos}). Backup: {backup}"
        )
    else:
        warning = ""

    if warning:
        return f"Rewrote {len(target)} tracks in '{pl['name']}'.\n" + _format_sort_preview(target) + warning

    return (
        f"Sorted {len(target)} tracks in '{pl['name']}' by artist and album.\n"
        + _format_sort_preview(target)
    )
```

Stub sementara untuk Task 4 (diganti di Task 5):

```python
def _rollback_sort(
    playlist_id: str, original: list[dict], removed: list[dict], backup: Path, reason: str
) -> str:
    return f"⚠️ Sort couldn't finish: {reason}. Backup: {backup}"
```

Test Task 4 tidak menyentuh jalur gagal, jadi stub ini cukup sampai Task 5.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server_playlist_sort.py -q`
Expected: semua pass.

---

### Task 5: Failure recovery

**Files:**
- Modify: `src/applemusic_mcp/server.py` (ganti stub `_rollback_sort`, tambahkan `_restore_playlist_tracks` sebelum `_playlist_sort_api`)
- Test: `tests/test_server_playlist_sort.py`

**Interfaces:**
- Consumes: `amp_api.get_tracks`, `amp_api.remove_track`, `amp_api.add_tracks`, `_SORT_BATCH`.
- Produces:
  - `_restore_playlist_tracks(playlist_id: str, tracks: list[dict]) -> tuple[bool, str]`
  - `_rollback_sort(playlist_id: str, original: list[dict], removed: list[dict], backup: Path, reason: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_playlist_sort.py -q`
Expected: FAIL — pesan restore belum ada.

- [ ] **Step 3: Implement recovery**

```python
def _restore_playlist_tracks(playlist_id: str, tracks: list[dict]) -> tuple[bool, str]:
    """Re-add tracks in the given order, batch 25, returning the first failure."""
    for i in range(0, len(tracks), _SORT_BATCH):
        batch = [(t["relationship_id"], "library-songs") for t in tracks[i : i + _SORT_BATCH]]
        ok, msg = amp_api.add_tracks(playlist_id, batch)
        if not ok:
            return False, msg
    return True, "Restored"


def _rollback_sort(
    playlist_id: str, original: list[dict], removed: list[dict], backup: Path, reason: str
) -> str:
    """Undo a half-finished rewrite: drop partial new entries, re-add what was removed."""
    original_ids = {t["relationship_id"] for t in original}
    current = amp_api.get_tracks(playlist_id)
    cleanup_error = ""
    if current is None:
        cleanup_error = " Couldn't read the playlist to clean up partial adds."
    else:
        for t in current:
            if t["relationship_id"] not in original_ids:
                ok, msg = amp_api.remove_track(playlist_id, t["relationship_id"])
                if not ok:
                    cleanup_error = f" Couldn't remove a partial add ({msg})."
                    break

    ok, msg = _restore_playlist_tracks(playlist_id, removed)
    restored = (
        f"Restored {len(removed)} track(s) to the original order."
        if ok
        else (
            f"Restore failed ({msg}) — the playlist may be missing tracks. "
            "Re-add them from the backup."
        )
    )
    return f"⚠️ Sort couldn't finish: {reason}. {restored}{cleanup_error}\nBackup: {backup}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server_playlist_sort.py -q`
Expected: semua pass.

---

### Task 6: Wire the `sort` action

**Files:**
- Modify: `src/applemusic_mcp/server.py` (`playlist()` di sekitar baris 3745: docstring, routing, fallback action list)
- Test: `tests/test_server_playlist_sort.py`

**Interfaces:**
- Consumes: `_playlist_sort_api`, `_label_write`.
- Produces: `playlist(action="sort", playlist=..., dry_run=...)`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_playlist_sort.py -q`
Expected: FAIL — `Unknown action: sort` / `sort` tidak ada di daftar.

- [ ] **Step 3: Implement routing + docstring**

Sisipkan sebelum `elif action == "copy":`:

```python
    elif action == "sort":
        return _label_write(_playlist_sort_api(playlist, dry_run), "web")
```

Ubah fallback action list menjadi:

```
list, folders, tracks, search, create, add, copy, sort, move, remove, delete, rename
```

Tambahkan ke docstring `playlist()`:

> `action='sort'` reorders a playlist in place by artist A–Z, then album A–Z and track number; `dry_run=True` previews it. Apple has no reorder API, so the playlist's tracks are rewritten (N removes + N/25 adds); only playlists created through this tool can be sorted.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server_playlist_sort.py -q`
Expected: semua pass.

---

### Task 7: Docs + full verification

**Files:**
- Modify: `docs/usage.md` (setelah bullet "Adding to a playlist")
- Modify: `README.md` (baris tabel tool `playlist`, sekitar baris 108)

- [ ] **Step 1: Add the usage bullet**

```markdown
- **Sorting a playlist? `playlist(action="sort", playlist=...)`.** Reorders in place by artist A–Z, then album A–Z and track number (case-insensitive; ties keep their old order); `dry_run=True` previews it. Apple has no reorder API, so this rewrites the playlist's tracks: N removes + N/25 adds, the playlist is briefly empty, and "date added" changes. Only playlists created through this tool can be sorted; a snapshot of the old order is saved under `~/.cache/applemusic-mcp/snapshots/`.
```

- [ ] **Step 2: Update the README tool table row**

```
| `playlist` | list, folders, tracks, search, create, add, copy, sort, move, remove, delete, rename |
```

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 4: Coverage gate**

Run: `make coverage`
Expected: `Required test coverage of 90% reached` atau lebih tinggi.
Jika `make` tidak tersedia di shell Windows: `python -m pytest --cov=applemusic_mcp --cov-fail-under=90 -q`.

- [ ] **Step 5: Lint and format**

Run: `ruff check .` lalu `black .`
Expected: `ruff` tanpa error; `black` tidak mengubah file di luar yang disentuh.

- [ ] **Step 6: Report status**

Laporkan angka pass/fail, coverage, dan hasil lint. Jangan klaim live-tested: pengujian ke akun nyata hanya setelah user setuju (`TEST_API=1`, playlist percobaan).

---

## Notes for the executor

- `amp_api.get_tracks` dipanggil dua kali pada jalur sukses (awal + verifikasi) dan sekali lagi saat rollback. Test yang memakai `fake_get` harus menyediakan urutan bacaan.
- `relationship_id` adalah id library-song (`i.xxx`); dipakai untuk hapus dan untuk tambah ulang sebagai `("i.xxx", "library-songs")` — sama seperti `_playlist_copy`.
- Jangan menambah dependency.
- Jangan commit tanpa permintaan user.
