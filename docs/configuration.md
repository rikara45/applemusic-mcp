# Configuration and engines

What the server may reach at all is set by the [permission level](../README.md#choose-how-much-it-can-reach).

## Engines

Two engines back this build. **API** is Apple Music's web API (`amp-api.music.apple.com`) for library, playlists, catalog, discovery — on any OS. **Chrome** runs a local Google Chrome window with MusicKit for DRM audio, which is where playback and the Up Next queue happen. A single `mode` preference picks playback routing: `auto` (default) and `chrome`/`web` all mean the Chrome web player; `api` means data only. Override a single playback/queue call with `engine=`. The [feature table](../README.md#features) shows what each engine can do.

## Settings

**Config file:** Claude Desktop uses `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS). Cursor, Cline, and Windsurf use the same `mcpServers` shape, see your client's docs.

**`mode` preference** picks playback routing: `auto` (default — the Chrome web player), `chrome`/`web` (the same), or `api` (data + writes, no playback). Set it conversationally: `config(action="set-pref", preference="mode", string_value="chrome")`. `mode` stays inside the [permission level](../README.md#choose-how-much-it-can-reach): a mode the level doesn't allow is refused, and one saved earlier is ignored. A per-call `engine=` overrides one playback or queue call. Using the queue makes its engine the active one, so transport controls reach it. Chrome runs an isolated window that stays signed in.

**Browser (Chrome web player) features** — playback and the Up Next queue — need Google Chrome plus Playwright and a desktop session (a real window is driven, so headless servers can't). Run the one-time browser download (`playwright install chromium`, or `uvx --from applemusic-mcp playwright install chromium`). The bundled Chromium can't decode Apple's DRM, so a real Chrome install is required. You don't sign in twice: one `applemusic-mcp login` covers the API and the web player.

**From source:** `git clone … && pip install -e .`, then point the config `command` at `<repo>/venv/bin/applemusic-mcp` or use `python -m applemusic_mcp`.
