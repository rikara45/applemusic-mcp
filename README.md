# applemusic-mcp

[![Release](https://img.shields.io/github/v/release/epheterson/applemusic-mcp.svg?label=release)](https://github.com/epheterson/applemusic-mcp/releases)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blue.svg)]()
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Downloads](https://static.pepy.tech/badge/applemusic-mcp)](https://pepy.tech/project/applemusic-mcp)
[![MCP](https://img.shields.io/badge/MCP-server-purple.svg)](https://modelcontextprotocol.io/)

MCP server for Apple Music. It gives any [MCP client](https://modelcontextprotocol.io/clients) (Claude, Cursor, Cline, Windsurf) control of your playlists, library, catalog, discovery, playback, and the Up Next queue. Runs on Windows and Linux, and on macOS through the Chrome web player.

**An Apple Music subscription and Google Chrome are all you need.**

*"Create a playlist called Road Trip and fill it with upbeat 90s alternative."* · *"Play my workout playlist on shuffle, and queue up Bohemian Rhapsody next."* · *"What have I been listening to lately, and what's topping the charts?"*

## Choose how much it can reach

You decide what this server may touch, in your MCP client's config. The assistant can't change it, and tools that need more than you allowed aren't offered to it at all.

| `--level` | It can reach | Good for |
|---|---|---|
| `api` | Apple's music service over the internet | Playlists, library, catalog, recommendations, discovery |
| `web` | + the Chrome web player | Playback and the Up Next queue |
| `full` | Everything | Every feature, nothing to decide |

Start at `api` and move to `web` when you want playback. Leaving `--level` off is the same as `full`.

For a Mac app with a guided setup instead of a terminal command, consider the community fork [secure-applemusic-mcp-for-osx](https://github.com/jaminben/secure-applemusic-mcp-for-osx).

## Features

What each engine can do, and the level that turns it on. `✓` supported, `✗` not possible there, `—` not applicable.

|Capability|Apple Music API · `api`|Chrome web player · `web`|
|---|:---:|:---:|
|**Catalog search / browse**|✓|—|
|Recommendations / charts / suggestions|✓|—|
|**Library search / browse**|✓|—|
|Recently played / added|✓|—|
|**Add catalog → library**|✓|—|
|Remove from library|✓|—|
|Love / dislike|✓|—|
|**Playlist** create / add / remove / delete / rename|✓|—|
|Playlist copy|✓|—|
|Folders: single level + move in/out|✓|—|
|**Playback**: play song / album / playlist / URL|—|✓|
|Controls: pause / stop / next / prev / seek|—|✓|
|Settings: volume / shuffle / repeat|—|✓|
|now_playing|—|✓|
|**Up Next queue**: view / next / last / remove / jump / clear / autoplay|—|✓|
|Reveal in app|—|✓ (navigates page)|
|**Cross-platform (Windows / Linux)**|✓|✓|

Playback and the queue need a desktop session, because they drive a real Chrome window
against `music.apple.com`. A few things the Music.app path used to add are gone in this
build: star ratings, the Favorites list, nested folders / folder tree, AirPlay, and
library snapshots. How playback is picked: [configuration](https://github.com/epheterson/applemusic-mcp/blob/main/docs/configuration.md).

## Setup

**Requirements:** Python 3.10+, an Apple Music subscription, and [Google Chrome](https://www.google.com/chrome/).

**Claude Code**, one line:

```bash
claude mcp add "Apple Music" -- uvx applemusic-mcp serve --level api
```

**Claude Desktop / Cursor / Cline / Windsurf**, install once, then add the config block:

```bash
pipx install applemusic-mcp        # or: pip install applemusic-mcp
playwright install chromium        # the Chrome web player: Windows/Linux, or a Mac with the [browser] extra
```

```json
{
  "mcpServers": {
    "Apple Music": {
      "command": "applemusic-mcp",
      "args": ["serve", "--level", "api"]
    }
  }
}
```

Change `api` to `web` to [unlock playback and the queue](#choose-how-much-it-can-reach). Config file locations, Chrome on a Mac, and source installs: [configuration](https://github.com/epheterson/applemusic-mcp/blob/main/docs/configuration.md).

Restart your client and try *"List my Apple Music playlists"* or *"Find songs like these three and build me a playlist."* Sign in once to reach your library, catalog, and playlists.

### Sign in

Needed for every level. Your password never touches this tool, and sign-in persists.

```bash
applemusic-mcp login            # opens Chrome on music.apple.com
applemusic-mcp status           # verify
```

`login` opens a Chrome window and waits for you to sign in (Apple ID + 2FA); the tool captures the session token and never sees your password. For bulk work (playlist imports, library migrations), use your own Apple developer token to avoid the shared rate limit. Details, the developer-token path, and which API each write uses: [signing in](https://github.com/epheterson/applemusic-mcp/blob/main/docs/sign-in.md).

## Tools

Seven action-based tools plus `vibe` keep the MCP context small. Each action-based tool takes an `action` and routes to the right engine.

| Tool | Actions |
|---|---|
| `playlist` | list, folders, tracks, search, create, add, copy, sort, move, remove, delete, rename |
| `library` | search, add, browse, recently_played, recently_added, rate, remove |
| `catalog` | search, resolve, album_tracks, album_details, song_details, artist_details, genres, suggestions |
| `discover` | recommendations, heavy_rotation, charts, top_songs, similar_artists, personal_station, song_station |
| `playback` | play (track / album / playlist / URL), control, now_playing, settings, reveal |
| `queue` | list, set, play_next, play_last, remove, jump, clear, autoplay (Up Next in the Chrome web player) |
| `config` | status, signin, logout, reset, set-pref, audit-log, clear-audit-log, list-storefronts |
| `vibe` | seed songs (+ optional candidates/excludes) → preview of same-feel tracks, then `create=true` to build the playlist |

Batching, importing playlists, dry runs, export formats and storefronts: [usage patterns](https://github.com/epheterson/applemusic-mcp/blob/main/docs/usage.md).

## CLI

```bash
applemusic-mcp serve --level api     # run the MCP server (your client calls this)
applemusic-mcp login                 # sign in through Chrome
applemusic-mcp login --dev           # your own Apple developer token (.p8)
applemusic-mcp logout                # sign out (switch accounts)
applemusic-mcp status                # auth status and permission level
applemusic-mcp reset --force         # wipe credentials (keeps your .p8 key file)
applemusic-mcp reset --all --force   # full uninstall: also removes the .p8, profile, and cache
```

## Good to know

- **Playback drives a real Chrome window.** Leave it signed in; `applemusic-mcp login` sets that up once. A headless or remote host has no window to drive, so playback and the queue need a desktop session.
- **Brand-new playlists take a moment** to be addable over the API (cloud propagation). The `vibe` tool retries briefly for exactly this reason; existing playlists are immediate.
- **Apple's rate limit is shared and rolling.** `applemusic-mcp login --dev` (your own developer token) gets a separate, much larger quota for bulk work.
- **If catalog actions start failing**, re-run `applemusic-mcp login`. The error text says whether the session expired or Apple is rate-limiting.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=epheterson/applemusic-mcp&type=Date)](https://star-history.com/#epheterson/applemusic-mcp&Date)

---

## License

MIT · *Unofficial community project — not affiliated with or endorsed by Apple. Uses your own Apple Music account for personal use; follow Apple's terms.*

<!-- Identifier for the official MCP Registry (PyPI ownership check). -->
mcp-name: io.github.epheterson/applemusic-mcp

## Credits

[FastMCP](https://github.com/jlowin/fastmcp) · [Apple MusicKit](https://developer.apple.com/documentation/applemusicapi) · [Model Context Protocol](https://modelcontextprotocol.io/)

---

Built with ❤️ in California by [@epheterson](https://github.com/epheterson) and [Claude Code](https://claude.com/claude-code).
