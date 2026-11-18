# Signing in

Sign-in unlocks everything this build does: the full catalog, your library, adding
songs, playlists, recommendations, playback and the Up Next queue. The terminal
`login` works at any level; signing in through the assistant needs `web` (the Chrome
web player is what it opens).

**Web sign-in** is what plain `applemusic-mcp login` does, and all most people need.
Your password never touches this tool, sign-in persists, and tokens re-fetch before
they expire. (It uses Apple's web-player API, the same path as open-source clients like
[Cider](https://github.com/ciderapp/Cider-2) and
[Music Assistant](https://www.music-assistant.io/music-providers/apple-music/).)

```bash
applemusic-mcp login            # opens Chrome to music.apple.com; sign in once
applemusic-mcp status           # verify
```

`login` opens a real Chrome window on `music.apple.com` and waits while you sign in
(Apple ID + 2FA). It captures the session token into the app's own Chrome profile, so
later playback and queue commands reuse it. Nothing runs on a headless server: the web
player needs a desktop session.

**Your own developer token, for bulk work.** Web sign-in uses Apple's *public*
web-player token, whose request quota is shared. Interactive use never gets near it, but
a few hundred catalog searches in an hour (a playlist import, a library migration) will
hit `HTTP 429`, and the window is **rolling and ~60 minutes long**, so retrying extends
it. The tool says so when you're throttled, rather than reporting "not found." With an
[Apple Developer Program](https://developer.apple.com/programs/) membership you get your
own, much larger quota, through Apple's sanctioned route:

1. [Developer Portal → Keys](https://developer.apple.com/account/resources/authkeys/list)
   → **+** → check **MusicKit** → Register → download the `.p8` (one time). Note your
   **Key ID** and **Team ID**.
2. Run `applemusic-mcp login --dev`. It prompts for the Team ID, Key ID and `.p8` path
   (or pass `--team-id`, `--key-id`, `--key-path`), writes the config, mints a 180-day
   token that renews on use, and authorizes you.

## What's sanctioned vs web

This build reaches Apple Music two ways and prefers the most official one available:

- **Apple Music API (sanctioned).** Your own developer token (from `login --dev`) against
  `api.music.apple.com`, Apple's documented API.
- **Web player (community path).** A token from a signed-in `music.apple.com` session
  against the web player's backend — the same approach as
  [Cider](https://github.com/ciderapp/Cider-2) and
  [Music Assistant](https://www.music-assistant.io/music-providers/apple-music/). It
  fills the few gaps the public API doesn't expose.

With a developer token, writes go through the sanctioned API; the web path is used only
for the operations Apple's public API can't do. With web sign-in alone, everything runs
on the web path. Each write tells you which path it took.

| Write | Apple Music API | Web player |
|---|:---:|:---:|
| Add to library | ✓ | ✓ |
| Create playlist | ✓ | ✓ |
| Add tracks (API-made playlist) | ✓ | ✓ |
| Love / dislike | ✓ | ✓ |
| Delete playlist | ✗ | ✓ |
| Rename / move into folder | partial | ✓ |

The `✗` cells are operations that path can't do, so they route to the other one. Both are
available at the `api` level; the columns are transports, not
[permission levels](../README.md#choose-how-much-it-can-reach). One Apple constraint to
know: playlists made in the Music app can't be written by either path here — create the
playlist through the API (which `vibe` and `playlist(action='create')` do) and it stays
writable.
