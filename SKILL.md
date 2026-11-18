---
name: apple-music
version: 0.24.0
description: Apple Music integration — the Apple Music API for library, playlists and catalog, plus the Chrome web player for playback and the Up Next queue
---

# Apple Music Integration

Guide for integrating with Apple Music through the API and the Chrome web player — cross-platform, no Music.app automation.

## When to Use

Invoke when users ask to:
- Manage playlists (create, add/remove tracks, list)
- Control playback (play, pause, skip, volume)
- Play an Apple Music URL (album, playlist, or song)
- Search catalog or library
- Add songs to library
- Access listening history or recommendations

## Permission Levels (MCP server)

The applemusic-mcp server runs at a level the user picks in their MCP client config: `api` (Apple's music APIs), `web` (+ the Chrome web player), or `full` (everything). If the server refuses something because of its level, tell the user what it needs and where to change it. Don't work around the refusal with a browser or anything in this guide: the user chose that limit.

## Critical Rule: Use the Tool, Not Raw Writes

Playlist writes go through `playlist(action='add')`; set `auto_add=True` when the song
isn't in the user's library yet. The server does the library-add + attach for you, and
skips duplicates. Do **not** call the playlist-tracks endpoint directly: it 500s for
playlists the API didn't create, and a partial write looks successful.

---

## Tool routing (when to use which top-level action)

When a user asks for something, the right MCP tool depends on whether they're searching their library or the catalog. Easy to confuse — gating wrong here leads users into "Developer token not found" rabbit holes when the operation could have worked tokenlessly.

| Goal | Use | Notes |
|---|---|---|
| Find a song the user already has | `library(action='search', query='...')` | Local library only. |
| Find a playlist by name | `playlist(action='list', filter='alice')` | Loose name match, returns matching playlists with IDs. Do **not** use `action='search'` for this — that searches the *tracks inside* a given playlist and needs a `playlist` param. |
| Find a song in Apple Music's full catalog | `catalog(action='search', query='...')` | API only. |
| Add a catalog song to the user's library | `library(action='add', track='...')` | Needs a developer token (generated or harvested) + a media-user-token (`login`). |
| Add a song (in library OR catalog) to a playlist | `playlist(action='add', track='...', auto_add=True)` | `auto_add=True` is required to reach the catalog when the song isn't already in the library. Default is False to avoid unwanted library writes. |
| Browse charts / recommendations | `discover` | API only. |
| Turn "I like these songs" into a playlist of similar ones | `vibe(seeds=[...])` | Previews by default; `create=true` + `name` builds it. Two API requests per seed (max 5 seeds), and creating adds every picked song to the library. |
| Play / control / queue | `playback`, `queue` | Chrome web player; `--level web` or higher. |

`library(action='search')` returns one page: `limit` (default 25) caps the results and `offset` pages through larger result sets. The text header shows `start-end of total`, so when `end < total` there are more results — bump `offset` by `limit` to fetch the next page (mirrors `action='browse'`).

If `library(action='search')` returns "No songs found", try `catalog(action='search')` or
`playlist(action='add', auto_add=True)` instead — the catalog search is a different store.

---

# MusicKit API

Cross-platform but requires Apple Developer account ($99/year) and token setup.

## Authentication

**Requirements:**
1. Apple Developer account
2. MusicKit key (.p8 file) from [developer portal](https://developer.apple.com/account/resources/authkeys/list)
3. Developer token (JWT, 180 day max)
4. User music token (browser OAuth)

**Generate developer token:**
```python
import jwt, datetime

with open('AuthKey_XXXXXXXXXX.p8') as f:
    private_key = f.read()

token = jwt.encode(
    {
        'iss': 'TEAM_ID',
        'iat': int(datetime.datetime.now().timestamp()),
        'exp': int((datetime.datetime.now() + datetime.timedelta(days=180)).timestamp())
    },
    private_key,
    algorithm='ES256',
    headers={'alg': 'ES256', 'kid': 'KEY_ID'}
)
```

**Get user token:** Browser OAuth to `https://authorize.music.apple.com/woa`

**Headers for all requests:**
```
Authorization: Bearer {developer_token}
Music-User-Token: {user_music_token}
```

**Base URL:** `https://api.music.apple.com/v1`

## Rate Limits (read this before any bulk loop)

Apple throttles with `HTTP 429` / code `42900` on a **rolling ~60-minute window**, and
sends **no `Retry-After` and no `X-Rate-Limit` header** — remaining quota is unobservable,
so you only find out by getting blocked.

| Token | Practical ceiling | Notes |
|---|---|---|
| Your own developer token (`login --dev`, your `.p8`) | ~3600 requests/hour, Apple's documented figure | Your quota alone |
| Apple's public web-player token (plain `login`) | a few hundred/hour, measured | Shared, not per-user — the ceiling is far lower and not yours to spend |

Consequences that bite:

- **Waiting briefly does nothing.** A 15-minute zero-traffic cooldown still 429s. It clears
  roughly an hour after the burst ends, then re-throttles after a few more requests.
  Every retry inside the window adds to the count keeping you blocked.
- **A 429 can look like an empty result, not an error.** One search per track ("resolve
  title+artist → catalog id") is the standard import shape and the fastest way to hit this;
  if the caller treats an empty search as "song not found", a throttle silently produces
  false negatives. Treat empty-after-429 as *unknown*, never as *absent*, and never cache it.
- **For bulk work, use `login --dev`.** Your own developer token gets its own, much larger
  quota. Reported and measured in [#42](https://github.com/epheterson/applemusic-mcp/issues/42).

### Batch-resolve by ISRC instead of searching per track

Where the caller has ISRCs (Spotify / Rekordbox / Plex exports all carry them),
`GET /v1/catalog/{storefront}/songs?filter[isrc]=ISRC1,ISRC2,…` resolves **25 per request**
and matches **exactly** — ~25x fewer requests than a search per track, and no fuzzy-match
errors. Via the tool: `catalog(action="resolve", isrcs="…", format="json")`.

Two things to get right, because `filter[isrc]` is a *filter*, not a search:

- **Misses are silent.** Apple returns only what it matched and omits the rest, so the
  unresolved ISRCs exist only as a diff of your request set against `attributes.isrc` on
  the responses. Compute that diff — don't assume a full response.
- **Keep "asked and absent" separate from "never asked."** If a batch fails (429) the
  remaining ISRCs have *unknown* status, not "not in the catalog." Collapsing the two
  recreates the false-negative bug this section exists to warn about.

One ISRC can map to several catalog songs (regional releases, remasters), so carry the
match count rather than silently taking the first.

**Without ISRCs, dry-run before you write.** Title matching is fuzzy, and its failures are
silent: `Dont Let Me Down` with no artist resolves to The Chainsmokers, not The Beatles.
Resolve the whole list first, show the proposed matches with a confidence marker (exact vs.
fuzzy), and only write once they've been reviewed — never interleave "search, then add"
per track. Via the tool: `catalog(action="resolve", tracks=..., format="json")`, which adds
nothing and returns `ids` ready for `playlist(action="add")` — or
`playlist(action="add", ..., dry_run=True)` to preview against a specific playlist,
which also diffs against what's already in it. Note this costs one request
per track (Apple has no batch title+artist endpoint), so it is *not* a rate-limit fix —
use ISRCs for that.

Manually:

```python
resolved = {}
for i in range(0, len(isrcs), 25):
    batch = isrcs[i : i + 25]
    r = requests.get(
        f"https://api.music.apple.com/v1/catalog/{storefront}/songs",
        headers=headers,
        params={"filter[isrc]": ",".join(batch)},
    )
    if r.status_code == 429:
        break                      # later batches only extend the window
    for song in r.json().get("data", []):
        resolved.setdefault(song["attributes"]["isrc"], song["id"])
```

## Available Endpoints

### Catalog (Public - dev token only)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/catalog/{storefront}/search` | GET | Search songs, albums, artists, playlists |
| `/catalog/{storefront}/songs/{id}` | GET | Song details |
| `/catalog/{storefront}/albums/{id}` | GET | Album details |
| `/catalog/{storefront}/albums/{id}/tracks` | GET | Album tracks |
| `/catalog/{storefront}/artists/{id}` | GET | Artist details |
| `/catalog/{storefront}/artists/{id}/albums` | GET | Artist's albums |
| `/catalog/{storefront}/artists/{id}/songs` | GET | Artist's top songs |
| `/catalog/{storefront}/artists/{id}/related-artists` | GET | Similar artists |
| `/catalog/{storefront}/playlists/{id}` | GET | Playlist details |
| `/catalog/{storefront}/charts` | GET | Top charts |
| `/catalog/{storefront}/genres` | GET | All genres |
| `/catalog/{storefront}/search/suggestions` | GET | Search autocomplete |
| `/catalog/{storefront}/stations/{id}` | GET | Radio station |

### Library (Requires user token)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/me/library/songs` | GET | All library songs |
| `/me/library/albums` | GET | All library albums |
| `/me/library/artists` | GET | All library artists |
| `/me/library/playlists` | GET | All library playlists |
| `/me/library/playlists/{id}` | GET | Playlist details |
| `/me/library/playlists/{id}/tracks` | GET | Playlist tracks |
| `/me/library/search` | GET | Search library |
| `/me/library` | POST | Add to library |
| `/catalog/{sf}/songs/{id}/library` | GET | Get library ID from catalog ID |

### Playlist Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/me/library/playlists` | POST | Create playlist |
| `/me/library/playlists/{id}/tracks` | POST | Add tracks to playlist |

### Personalization

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/me/recommendations` | GET | Personalized recommendations |
| `/me/history/heavy-rotation` | GET | Frequently played |
| `/me/recent/played` | GET | Recently played |
| `/me/recent/added` | GET | Recently added |

### Ratings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/me/ratings/songs/{id}` | GET | Get song rating |
| `/me/ratings/songs/{id}` | PUT | Set song rating |
| `/me/ratings/songs/{id}` | DELETE | Remove rating |
| `/me/ratings/albums/{id}` | GET/PUT/DELETE | Album ratings |
| `/me/ratings/playlists/{id}` | GET/PUT/DELETE | Playlist ratings |

### Storefronts

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/storefronts` | GET | All storefronts |
| `/storefronts/{id}` | GET | Storefront details |
| `/me/storefront` | GET | User's storefront |

## Common Query Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `term` | Search query | `term=beatles` |
| `types` | Resource types | `types=songs,albums` |
| `limit` | Results per page (max 25) | `limit=10` |
| `offset` | Pagination offset | `offset=25` |
| `include` | Related resources | `include=artists,albums` |
| `extend` | Additional attributes | `extend=editorialNotes` |
| `l` | Language code | `l=en-US` |

## Search Example

```bash
GET /v1/catalog/us/search?term=wonderwall&types=songs&limit=10

Response:
{
  "results": {
    "songs": {
      "data": [{
        "id": "1234567890",
        "type": "songs",
        "attributes": {
          "name": "Wonderwall",
          "artistName": "Oasis",
          "albumName": "(What's the Story) Morning Glory?",
          "durationInMillis": 258773,
          "releaseDate": "1995-10-02",
          "genreNames": ["Alternative", "Music"]
        }
      }]
    }
  }
}
```

## Library-First Workflow (Complete)

Adding a catalog song to a playlist requires 4 API calls:

```python
import requests

headers = {
    "Authorization": f"Bearer {dev_token}",
    "Music-User-Token": user_token
}

# 1. Search catalog
r = requests.get(
    "https://api.music.apple.com/v1/catalog/us/search",
    headers=headers,
    params={"term": "Wonderwall Oasis", "types": "songs", "limit": 1}
)
catalog_id = r.json()['results']['songs']['data'][0]['id']

# 2. Add to library
requests.post(
    "https://api.music.apple.com/v1/me/library",
    headers=headers,
    params={"ids[songs]": catalog_id}
)

# 3. Get library ID (catalog ID → library ID)
r = requests.get(
    f"https://api.music.apple.com/v1/catalog/us/songs/{catalog_id}/library",
    headers=headers
)
library_id = r.json()['data'][0]['id']

# 4. Add to playlist (library IDs only!)
requests.post(
    f"https://api.music.apple.com/v1/me/library/playlists/{playlist_id}/tracks",
    headers={**headers, "Content-Type": "application/json"},
    json={"data": [{"id": library_id, "type": "library-songs"}]}
)
```

## Create Playlist

```bash
POST /v1/me/library/playlists
Content-Type: application/json

{
  "attributes": {
    "name": "Road Trip",
    "description": "Summer vibes"
  },
  "relationships": {
    "tracks": {
      "data": []
    }
  }
}
```

## Ratings

```bash
# Love a song (value: 1 = love, -1 = dislike)
PUT /v1/me/ratings/songs/{id}
Content-Type: application/json

{"attributes": {"value": 1}}
```

## Limitations

- **No playback control** - API cannot play/pause/skip
- **Playlist editing** - can only modify API-created playlists
- **Token management** - dev tokens expire every 180 days
- **Rate limits** - Apple enforces request limits

---

# Common Mistakes

**❌ Using catalog IDs in playlists:**
```python
# WRONG
json={"data": [{"id": "1234567890", "type": "songs"}]}
```
**Fix:** Add to library first, get library ID, then add.

**❌ Expired tokens:**
Dev tokens last 180 days max.
**Fix:** Check expiration, handle 401 errors.

---

# The Easy Way: applemusic-mcp

The [applemusic-mcp](https://github.com/epheterson/applemusic-mcp) MCP server handles all this complexity automatically: token management, the library-add + attach flow, ID conversions, and rate-limit reporting.

**Install:**
```bash
git clone https://github.com/epheterson/applemusic-mcp.git
cd applemusic-mcp && python3 -m venv venv && source venv/bin/activate
pip install -e .
```

**Configure Claude Desktop:**
```json
{
  "mcpServers": {
    "Apple Music": {
      "command": "/path/to/applemusic-mcp/venv/bin/python",
      "args": ["-m", "applemusic_mcp"]
    }
  }
}
```

Sign in once (`applemusic-mcp login`), set `--level api` (or `web` for playback), and see the repo README for setup.

| Manual | applemusic-mcp |
|--------|----------------|
| 4 API calls to add song | `playlist(action="add", auto_add=True)` |
| Copy URL + open in Music | `playback(action="play", url="...")` |
| Add a catalog song to your library | `library(action="add")` (unified API — dev or web token) |
| Token management | Automatic with warnings |
