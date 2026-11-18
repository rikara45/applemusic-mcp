"""Vibe playlist: a few seed songs in, a list of songs with the same feel out.

The pipeline is resolve → harvest → rank. It talks to Apple only through
``amp_api`` (no HTTP of its own), so it can be tested without a network, and the
ranking is deterministic: same input, same order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from . import amp_api

MAX_SEEDS = 5  # 2 requests each (metadata + radio draw) against the rolling quota
_RADIO = "radio"  # source tag: Apple's per-song station
_CANDIDATE = "candidate"  # source tag: suggested by the calling model


@dataclass
class VibePlan:
    """Everything the tool needs to render a preview or a create result."""

    tracks: list[dict] = field(default_factory=list)  # ranked picks: track/score/why
    seeds: list[dict] = field(default_factory=list)  # seeds that actually resolved
    seed_misses: list[str] = field(default_factory=list)  # seeds not found in the catalog
    harvest_skips: list[str] = field(default_factory=list)  # seeds whose radio came back empty
    candidate_misses: list[str] = field(default_factory=list)  # candidates not found
    err: str = ""  # "" | "expired" | "throttled" — the caller words these


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def _year(track: dict) -> int | None:
    d = (track.get("release_date") or "")[:4]
    return int(d) if d.isdigit() else None


def _pick(matches: list[dict], term: str) -> dict | None:
    """Exact title match if there is one, else the search's own first result."""
    q = _norm(term)
    for m in matches:
        if _norm(m.get("name")) == q:
            return m
    return matches[0] if matches else None


def resolve(items: list[str] | None, storefront: str = "") -> tuple[list[dict], list[str]]:
    """Seed/candidate strings → catalog tracks. Returns (tracks, misses). One
    request each; a catalog id is read directly instead of searched."""
    out: list[dict] = []
    misses: list[str] = []
    for raw in items or []:
        q = (raw or "").strip()
        if not q:
            continue
        song = amp_api.catalog_song(q, storefront) if q.isdigit() else None
        if song is None:
            song = _pick(amp_api.search_catalog_songs(q, 5, storefront), q)
        if song is None:
            misses.append(q)
        elif all(t["id"] != song["id"] for t in out):
            out.append(song)
    return out, misses


def _excluded(track: dict, ids: set[str], terms: set[str]) -> bool:
    if track["id"] in ids:
        return True
    hay = f"{_norm(track.get('name'))} {_norm(track.get('artist'))}"
    # Loose text match only for a real title; a 1-2 char exclude would otherwise
    # substring-match nearly every song.
    return any(len(t) > 2 and t in hay for t in terms)


def rank(seeds: list[dict], pool: list[dict], count: int) -> list[dict]:
    """Score, sort and cut the candidate pool. Ties keep the original order
    (model suggestions first, then each seed's radio draw in seed order)."""
    seed_genres = {_norm(g) for s in seeds for g in s.get("genres") or []}
    seed_artists = {_norm(s.get("artist")) for s in seeds}
    years = [y for y in (_year(s) for s in seeds) if y]
    mid = median(years) if years else None

    scored = []
    for order, t in enumerate(pool):
        genres = {_norm(g) for g in t.get("genres") or []}
        shared = genres & seed_genres
        score = 0.0
        why = []
        if shared:
            score += 3 * len(shared) / len(genres)
            why.append("/".join(sorted(shared)))
        if _norm(t.get("artist")) in seed_artists:
            score += 2
            why.append("artis seed")
        year = _year(t)
        if year is not None and mid is not None:
            score += 1 - min(1, abs(year - mid) / 15)
            why.append(str(year))
        if t.get("source") == _CANDIDATE:
            score += 0.5
        scored.append((score, order, t, why))

    scored.sort(key=lambda s: (-s[0], s[1]))
    return [
        {"track": t, "score": round(score, 2), "why": why}
        for score, _, t, why in scored[:count]
    ]


def build(
    seeds: list[str],
    candidates: list[str] | None = None,
    exclude: list[str] | None = None,
    count: int = 25,
    storefront: str = "",
) -> VibePlan:
    """Run the whole pipeline once. Callers re-run it for create, on purpose:
    Apple's radio is not reproducible, so a preview is not a shopping list."""
    plan = VibePlan()
    seed_tracks, plan.seed_misses = resolve(seeds, storefront)
    plan.seeds = seed_tracks
    if not seed_tracks:
        return plan

    suggested, plan.candidate_misses = resolve(candidates, storefront)
    for t in suggested:
        t["source"] = _CANDIDATE

    pool, plan.harvest_skips, plan.err = _harvest(seed_tracks)
    if plan.err:
        return plan

    dropped, _ = resolve(exclude, storefront)
    drop_ids = {s["id"] for s in seed_tracks} | {d["id"] for d in dropped}
    # An exclude that didn't resolve still gets a chance as loose text.
    drop_terms = {_norm(q) for q in exclude or []} | {
        f"{_norm(d['name'])} {_norm(d['artist'])}" for d in dropped
    }

    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    kept: list[dict] = []
    for t in suggested + pool:
        pair = (_norm(t.get("name")), _norm(t.get("artist")))
        if t["id"] in seen_ids or pair in seen_pairs or _excluded(t, drop_ids, drop_terms):
            continue
        seen_ids.add(t["id"])
        seen_pairs.add(pair)
        kept.append(t)

    plan.tracks = rank(seed_tracks, kept, count)
    return plan


def _harvest(seeds: list[dict]) -> tuple[list[dict], list[str], str]:
    pool: list[dict] = []
    skipped: list[str] = []
    for seed in seeds:
        tracks, err = amp_api.next_tracks(seed["id"])
        if err in ("expired", "throttled"):
            return pool, skipped, err
        if err:
            skipped.append(f"{seed['name']} ({err})")
        elif not tracks:
            skipped.append(seed["name"])
        else:
            pool.extend({**t, "source": _RADIO} for t in tracks)
    return pool, skipped, ""
