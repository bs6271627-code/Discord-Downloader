---
name: YouTube/Spotify audio routing on Replit
description: Why the Lavalink youtube-plugin fails on Replit and how the play command works around it
---

# YouTube / Spotify on Replit — routing decision

## The problem
Replit datacenter IPs are blocked by YouTube at the **player API** level.
- Lavalink youtube-plugin (ANDROID_MUSIC + IOS clients) → `AllClientsFailedException: This video requires login`
- yt-dlp → "Sign in to confirm you're not a bot"
- Both ytsearch: and direct URLs fail; `loadType: "error"` or `"empty"`

Spotify resolution also fails because the youtube-plugin uses YouTube search underneath.

SoundCloud works perfectly (confirmed). Bandcamp, Vimeo, Twitch, HTTP streams also work.

**Why:** Same IP block applies regardless of client. Confirmed July 2026.

## The fix — implemented in `bot/cogs/music.py`

All YouTube/Spotify content is resolved to a SoundCloud track using lightweight metadata APIs, then played via SoundCloud.

| Input | Metadata source | SoundCloud query |
|---|---|---|
| YouTube video URL | YouTube oEmbed API (`youtube.com/oembed?url=…&format=json`) | `title [+ author_name if not already in title]` |
| YouTube video (oEmbed 404) | ytInitialData `"title":{"runs":[{"text":"…"` | title |
| YouTube playlist URL | Public Atom/RSS feed (`youtube.com/feeds/videos.xml?playlist_id=…`) | each title, ≤25, parallelized |
| Spotify track URL | Spotify public oEmbed (`open.spotify.com/oembed?url=…`) | `title` only (no artist in oEmbed) |
| Spotify album/playlist | Spotify oEmbed (name only) | informative error — no free track listing API |
| Text query | — | direct SoundCloud search |
| Other URL | — | Lavalink http source directly |

## Key facts
- YouTube oEmbed (`youtube.com/oembed`) uses **different infrastructure** from the player API — works from datacenter IPs ✅
- YouTube RSS/Atom feeds for playlists work from datacenter IPs ✅
- Spotify oEmbed (`open.spotify.com/oembed`) returns `title` but NO `author_name` — artist is not available without Spotify API credentials
- Spotify embed page and open.spotify.com pages are Next.js SPAs with no useful SSR metadata
- Spotify API (`api.spotify.com/v1/tracks/{id}`) returns 401 without credentials

**Why:** Adding YouTube OAuth would fix the youtube-plugin, but requires a Google account token from the user. The oEmbed + SoundCloud approach requires no credentials and is stable.

**How to apply:** Any change to the play command routing logic must preserve the oEmbed-first path. Never send YouTube or Spotify URLs directly to `wavelink.Playable.search()` — they return `loadType: "error"` or `"empty"`.

## Future improvements possible
- Add Spotify API client credentials (requires `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` secrets) → enables artist name lookup and album/playlist track listings
- Add YouTube OAuth token to youtube-plugin config → enables direct YouTube playback without SoundCloud hop
