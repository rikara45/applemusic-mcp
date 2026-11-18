"""Vibe playlist: the pipeline (vibe.py) and the tool's preview/create wording.

Network is mocked at the ``amp_api`` boundary; the real ranking, dedupe and
filtering run, so a change in how tracks are picked shows up here.
"""

import pytest

from applemusic_mcp import amp_api, server, vibe


def song(sid, name, artist, genres=("Pop", "Music"), date="2010-05-01", source=""):
    t = {
        "id": sid,
        "name": name,
        "artist": artist,
        "genres": list(genres),
        "release_date": date,
    }
    if source:
        t["source"] = source
    return t


# --- resolve ---------------------------------------------------------------


@pytest.fixture
def catalog(monkeypatch):
    """Term → search hits, and id → single song. Anything else is a miss."""
    hits = {
        "Seed One": [song("s1", "Seed One", "A")],
        "Seed Two": [song("s2", "Seed Two", "B")],
        "Cand": [song("c1", "Candidate", "C")],
        "Excluded": [song("x1", "Excluded", "D")],
    }
    by_id = {"999": song("999", "By Id", "E")}
    calls = []

    def search(term, limit=5, storefront=""):
        calls.append(("search", term, storefront))
        return hits.get(term, [])

    def single(song_id, storefront=""):
        calls.append(("id", song_id, storefront))
        return by_id.get(song_id)

    monkeypatch.setattr(vibe.amp_api, "search_catalog_songs", search)
    monkeypatch.setattr(vibe.amp_api, "catalog_song", single)
    return calls


def test_resolve_by_name_id_and_misses(catalog):
    tracks, misses = vibe.resolve(["Seed One", "999", "nothing here", "  "], "id")
    assert [t["id"] for t in tracks] == ["s1", "999"]
    assert misses == ["nothing here"]
    assert ("search", "Seed One", "id") in catalog


def test_resolve_dedupes_same_song(catalog):
    tracks, _ = vibe.resolve(["Seed One", "Seed One"], "us")
    assert [t["id"] for t in tracks] == ["s1"]


# --- ranking ---------------------------------------------------------------


def test_rank_scores_genre_artist_era_and_candidates():
    seeds = [song("s1", "S", "A", ["Pop", "Music"], "2010-05-01")]
    pool = [
        song("r1", "R1", "A", ["Rock", "Music"], "2010-06-01", "radio"),
        song("r2", "R2", "Z", ["Rock", "Music"], "1990-01-01", "radio"),
        song("c1", "C1", "B", ["Pop", "Music"], "2011-01-01", "candidate"),
    ]
    ranked = vibe.rank(seeds, pool, 3)
    # seed artist + shared genre beats the candidate bonus; the 1990 track falls last
    assert [p["track"]["id"] for p in ranked] == ["r1", "c1", "r2"]
    assert "artis seed" in ranked[0]["why"]
    assert ranked[1]["track"]["source"] == "candidate"


def test_rank_ties_keep_pool_order():
    seeds = [song("s1", "S", "A", ["Pop"], "2010-01-01")]
    pool = [
        song("c1", "Same", "X", ["Pop"], "", "candidate"),
        song("r1", "Same", "Y", ["Pop"], "", "radio"),
    ]
    # candidate gets +0.5, so make the radio one earlier to prove order breaks ties
    ranked = vibe.rank(seeds, pool, 2)
    assert [p["track"]["id"] for p in ranked] == ["c1", "r1"]


def test_rank_without_metadata_still_scores():
    seeds = [song("s1", "S", "A", [], "")]
    ranked = vibe.rank(seeds, [song("r1", "R", "Z", [], "")], 5)
    assert ranked[0]["score"] == 0.0
    assert ranked[0]["why"] == []


def test_rank_cuts_to_count():
    seeds = [song("s1", "S", "A")]
    pool = [song(f"r{i}", f"R{i}", "Z") for i in range(10)]
    assert len(vibe.rank(seeds, pool, 3)) == 3


# --- build -----------------------------------------------------------------


@pytest.fixture
def pipeline(monkeypatch):
    """Resolve/harvest stubs for build(). Returns the recorded next_tracks calls."""
    monkeypatch.setattr(
        vibe.amp_api,
        "search_catalog_songs",
        lambda term, limit=5, storefront="": {
            "Seed One": [song("s1", "Seed One", "A")],
            "Seed Two": [song("s2", "Seed Two", "B")],
            "Cand": [song("c1", "Cand", "C")],
            "Excluded": [song("x1", "X One", "D")],
            "Rita": [],
        }.get(term, []),
    )
    monkeypatch.setattr(vibe.amp_api, "catalog_song", lambda sid, storefront="": None)
    draws = {
        "s1": [song("s1", "Seed One", "A"), song("r1", "R1", "A"), song("r2", "R2", "B")],
        "s2": [song("r3", "R3", "B"), song("r4", "Rita", "Z", ["Jazz"], "1970-01-01")],
    }

    def next_tracks(sid):
        return draws[sid], ""

    monkeypatch.setattr(vibe.amp_api, "next_tracks", next_tracks)
    return draws


def test_build_drops_seeds_dupes_and_excludes(pipeline):
    plan = vibe.build(
        ["Seed One", "Seed Two"],
        candidates=["Cand", "not in catalog"],
        exclude=["Excluded", "Rita"],
        count=10,
    )
    ids = [p["track"]["id"] for p in plan.tracks]
    assert "s1" not in ids and "s2" not in ids  # seeds never come back
    assert ids.count("r1") == 1  # radio returned the seed too; dedupe holds
    assert "x1" not in ids and "r4" not in ids  # resolved + loose exclude
    assert "c1" in ids
    assert plan.candidate_misses == ["not in catalog"]


def test_build_ignores_a_too_short_exclude_term(pipeline):
    plan = vibe.build(["Seed One"], exclude=["R"], count=10)
    assert "r1" in [p["track"]["id"] for p in plan.tracks]


def test_build_reports_unresolved_seed(pipeline):
    plan = vibe.build(["Seed One", "nope"], count=5)
    assert [s["id"] for s in plan.seeds] == ["s1"]
    assert plan.seed_misses == ["nope"]


def test_build_no_seed_stops_before_harvest(monkeypatch):
    monkeypatch.setattr(vibe.amp_api, "search_catalog_songs", lambda *a, **k: [])
    monkeypatch.setattr(vibe.amp_api, "catalog_song", lambda *a, **k: None)
    monkeypatch.setattr(
        vibe.amp_api, "next_tracks", lambda sid: pytest.fail("must not harvest")
    )
    plan = vibe.build(["nothing"])
    assert plan.seeds == [] and plan.seed_misses == ["nothing"] and plan.tracks == []


def test_build_skips_empty_radio(pipeline, monkeypatch):
    monkeypatch.setattr(
        vibe.amp_api,
        "next_tracks",
        lambda sid: ([], "") if sid == "s1" else ([song("r3", "R3", "B")], ""),
    )
    plan = vibe.build(["Seed One", "Seed Two"], count=5)
    assert plan.harvest_skips == ["Seed One"]
    assert [p["track"]["id"] for p in plan.tracks] == ["r3"]


@pytest.mark.parametrize("err", ["expired", "throttled"])
def test_build_aborts_on_session_or_throttle(pipeline, monkeypatch, err):
    monkeypatch.setattr(
        vibe.amp_api,
        "next_tracks",
        lambda sid: ([], err) if sid == "s2" else ([song("r1", "R1", "A")], ""),
    )
    plan = vibe.build(["Seed One", "Seed Two"], count=5)
    assert plan.err == err
    assert plan.tracks == []


def test_build_notes_failed_radio_with_reason(pipeline, monkeypatch):
    monkeypatch.setattr(vibe.amp_api, "next_tracks", lambda sid: ([], "status 500"))
    plan = vibe.build(["Seed One"], count=5)
    assert plan.harvest_skips == ["Seed One (status 500)"]


# --- the tool --------------------------------------------------------------


@pytest.fixture
def tool(monkeypatch):
    """Server-level boundary stubs. Every test can override one of these."""
    state = {"draws": {}, "searches": {}, "next_calls": [], "created": [], "added": []}

    monkeypatch.setattr(
        server.amp_api, "resolve_playlist", lambda name, api_created_only=True: None
    )
    monkeypatch.setattr(
        server.amp_api,
        "search_catalog_songs",
        lambda term, limit=5, storefront="": state["searches"].get(term, []),
    )
    monkeypatch.setattr(server.amp_api, "catalog_song", lambda sid, storefront="": None)
    monkeypatch.setattr(server.amp_api, "throttled_recently", lambda *a, **k: False)
    monkeypatch.setattr(server, "_can_use_library_api", lambda: True)
    monkeypatch.setattr(server, "_has_user_token", lambda: True)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)

    def next_tracks(sid):
        state["next_calls"].append(sid)
        draw = state["draws"].get(sid, [])
        return draw if isinstance(draw, tuple) else (draw, "")

    def create(name, description=""):
        state["created"].append(name)
        return True, "p.NEW"

    def add(pid, ids):
        state["added"].append(list(ids))
        return True, "Added"

    monkeypatch.setattr(server.amp_api, "next_tracks", next_tracks)
    monkeypatch.setattr(server.amp_api, "create_playlist", create)
    monkeypatch.setattr(server.amp_api, "add_tracks", add)
    state["searches"].update(
        {
            "seed": [song("s1", "Seed", "A", ["Pop", "Music"], "2010-05-01")],
            "cand": [song("c1", "Cand", "C", ["Pop", "Music"], "2012-01-01")],
        }
    )
    state["draws"] = {
        "s1": [song("r1", "R1", "A", ["Pop", "Music"], "2011-01-01")],
    }
    return state


def test_tool_preview_writes_nothing(tool):
    out = server.vibe(seeds=["seed"])
    assert "pratinjau" in out
    assert "1. R1 — A (Pop, 2011)" in out
    assert "ditambahkan ke library" in out
    assert "create=true" in out
    assert tool["created"] == [] and tool["added"] == []


def test_tool_preview_marks_candidates_and_misses(tool):
    tool["searches"]["missing"] = []
    out = server.vibe(seeds=["seed"], candidates=["cand", "missing"])
    assert "usulan · " in out
    assert "Usulan tidak ditemukan: missing" in out


def test_tool_requires_name_for_create(tool):
    out = server.vibe(seeds=["seed"], create=True)
    assert "name wajib" in out
    assert tool["next_calls"] == []


def test_tool_rejects_duplicate_playlist_name(tool, monkeypatch):
    monkeypatch.setattr(
        server.amp_api,
        "resolve_playlist",
        lambda name, api_created_only=True: {"id": "p.OLD", "name": name, "canEdit": True},
    )
    out = server.vibe(seeds=["seed"], name="Vibe 1", create=True)
    assert "sudah ada" in out
    assert tool["created"] == [] and tool["next_calls"] == []


def test_tool_warns_on_similar_name(tool, monkeypatch):
    monkeypatch.setattr(
        server.amp_api,
        "resolve_playlist",
        lambda name, api_created_only=True: {
            "id": "p.OLD",
            "name": "Vibe Hits Lama",
            "canEdit": True,
        },
    )
    out = server.vibe(seeds=["seed"], name="Vibe Hits")
    assert "namanya mirip" in out


def test_tool_create_adds_tracks_in_batches(tool):
    tool["draws"]["s1"] = [song(f"r{i}", f"R{i}", "A") for i in range(30)]
    out = server.vibe(seeds=["seed"], name="Vibe 1", count=30, create=True)
    assert tool["created"] == ["Vibe 1"]
    assert [len(b) for b in tool["added"]] == [25, 5]
    assert "30/30 lagu" in out
    assert "30 lagu ditambahkan ke library" in out
    assert out.count("— A") >= 30  # the landed tracks are listed, not a preview


def test_tool_create_retries_then_reports_landed(tool, monkeypatch):
    attempts = {"n": 0}

    def flaky(pid, ids):
        attempts["n"] += 1
        return (attempts["n"] > 1), ("boom" if attempts["n"] == 1 else "Added")

    monkeypatch.setattr(server.amp_api, "add_tracks", flaky)
    out = server.vibe(seeds=["seed"], name="Vibe 1", create=True)
    assert attempts["n"] == 2
    assert "1/1 lagu" in out


def test_tool_create_reports_failed_batch(tool, monkeypatch):
    monkeypatch.setattr(server.amp_api, "add_tracks", lambda pid, ids: (False, "500 nope"))
    out = server.vibe(seeds=["seed"], name="Vibe 1", create=True)
    assert "Batch gagal" in out and "500 nope" in out
    assert "0/1 lagu" in out


def test_tool_create_failure_returns_error(tool, monkeypatch):
    monkeypatch.setattr(
        server.amp_api, "create_playlist", lambda name, description="": (False, "403 no")
    )
    out = server.vibe(seeds=["seed"], name="Vibe 1", create=True)
    assert out.startswith("Error: gagal membuat playlist")


def test_tool_caps_seeds_before_any_request(tool):
    out = server.vibe(seeds=[f"s{i}" for i in range(vibe.MAX_SEEDS + 1)])
    assert "maksimal" in out
    assert tool["next_calls"] == []


@pytest.mark.parametrize("bad", [[], ["  "]])
def test_tool_needs_a_seed(tool, bad):
    assert "minimal satu seed" in server.vibe(seeds=bad)


def test_tool_rejects_zero_count(tool):
    assert "count" in server.vibe(seeds=["seed"], count=0)


def test_tool_maps_throttled_harvest(tool):
    tool["draws"]["s1"] = ([], "throttled")
    out = server.vibe(seeds=["seed"])
    assert "429" in out and "rate-limiting" in out


def test_tool_maps_expired_session(tool):
    tool["draws"]["s1"] = ([], "expired")
    out = server.vibe(seeds=["seed"])
    assert "expired" in out and "applemusic-mcp login" in out


def test_tool_reports_missing_session_before_blaming_the_catalog(tool, monkeypatch):
    tool["searches"]["seed"] = []
    monkeypatch.setattr(server, "_has_user_token", lambda: False)
    out = server.vibe(seeds=["seed"])
    assert "expired" in out and "applemusic-mcp login" in out


def test_tool_explains_throttled_empty_search(tool, monkeypatch):
    tool["searches"]["seed"] = []
    monkeypatch.setattr(server.amp_api, "throttled_recently", lambda *a, **k: True)
    out = server.vibe(seeds=["seed"])
    assert "429" in out and "Seed dicoba: seed" in out


def test_tool_reports_nothing_left_after_filtering(tool, monkeypatch):
    tool["draws"]["s1"] = [song("s1", "Seed", "A")]  # only the seed itself
    out = server.vibe(seeds=["seed"])
    assert "tidak ada lagu yang lolos" in out


def test_tool_passes_storefront_through(tool, monkeypatch):
    seen = {}

    def search(term, limit=5, storefront=""):
        seen["sf"] = storefront
        return [song("s1", "Seed", "A")]

    monkeypatch.setattr(server.amp_api, "search_catalog_songs", search)
    server.vibe(seeds=["seed"], storefront="id")
    assert seen["sf"] == "id"


def test_tool_fewer_tracks_than_count(tool):
    out = server.vibe(seeds=["seed"], count=25)
    assert "pratinjau 1 lagu" in out
    assert "1 lagu ini akan ditambahkan" in out


def test_next_tracks_contract():
    """The tool trusts this contract exactly; keep it pinned."""
    assert amp_api._NEXT_TRACKS_LIMIT == 10
