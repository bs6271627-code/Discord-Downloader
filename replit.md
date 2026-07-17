# Discord Music Bot

A Discord bot that plays music in voice channels using slash commands (`/play`, etc.), powered by discord.py + Wavelink + a bundled Lavalink audio server.

## Run & Operate

Two workflows must both be running:

- **Lavalink** — `cd lavalink && java -jar Lavalink.jar` — Java audio server (port 2333)
- **Discord Music Bot** — `uv run python bot/main.py` — Python bot that connects to Lavalink

Start both via the **Project** run button (runs them in parallel).

To install / sync Python dependencies: `uv sync` (creates `.pythonlibs/` venv from `pyproject.toml`).

## Stack

- Python 3.12, discord.py 2.x, Wavelink 3.x, yt-dlp
- Lavalink 4.x (Java 21) with youtube-plugin 1.18.1
- Audio sources: YouTube, SoundCloud, Bandcamp, Twitch, Vimeo, HTTP streams

## Where things live

- `bot/main.py` — bot entrypoint, Lavalink connection logic
- `bot/cogs/music.py` — all music slash commands
- `lavalink/application.yml` — Lavalink server config (password, sources, plugins)
- `pyproject.toml` — Python dependencies

## Required secrets

- `TOKEN` — Discord bot token (set as a Replit Secret)

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- Start **Lavalink first** — the bot retries connecting up to 12 times (5 s apart), so if Lavalink is slow to start the bot will wait and reconnect automatically.
- Python deps must be installed via `uv sync`, not pip — NixOS's system Python is externally managed.
- The Lavalink password (`youshallnotpass`) is hardcoded in both `bot/main.py` and `lavalink/application.yml` — change both if you rotate it.
