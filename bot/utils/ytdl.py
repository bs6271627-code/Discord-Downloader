import asyncio
import discord
import yt_dlp

# Suppress yt-dlp noise — must accept *args/**kwargs; newer yt-dlp calls
# bug_reports_message(before='\n') which breaks a plain `lambda: ""`.
def _no_bug_report(*args, **kwargs) -> str:
    return ""

yt_dlp.utils.bug_reports_message = _no_bug_report

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


class YTDLSource:
    """Wraps a yt-dlp result into a discord.FFmpegPCMAudio source."""

    def __init__(self, source: discord.FFmpegPCMAudio, *, data: dict):
        self.source = source
        self.data = data
        self.title: str = data.get("title", "Unknown")
        self.url: str = data.get("webpage_url", "")
        self.duration = data.get("duration")
        self.thumbnail: str | None = data.get("thumbnail")
        self.uploader: str = data.get("uploader", "Unknown")

    @classmethod
    async def from_query(cls, query: str) -> "YTDLSource":
        """
        Resolve a YouTube URL or search query and return a YTDLSource.
        The blocking yt-dlp call runs in a thread-pool executor.
        Uses asyncio.get_running_loop() — correct for Python 3.10+ inside
        a running event loop (get_event_loop() is deprecated there).
        """
        loop = asyncio.get_running_loop()

        if not query.startswith(("http://", "https://")):
            query = f"ytsearch:{query}"

        print(f"[ytdl] extraction start  query={query!r}", flush=True)

        def _extract() -> dict:
            print("[ytdl] _extract running in executor thread", flush=True)
            with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
                info = ydl.extract_info(query, download=False)
                print(f"[ytdl] extract_info returned: {type(info)}", flush=True)
                if info is None:
                    raise ValueError("yt-dlp returned no data for that query.")
                if "entries" in info:
                    entries = [e for e in info["entries"] if e]
                    if not entries:
                        raise ValueError("No results found for that query.")
                    info = entries[0]
                    print(f"[ytdl] picked first search entry: {info.get('title')!r}", flush=True)
                return ydl.sanitize_info(info)

        data = await loop.run_in_executor(None, _extract)
        print(f"[ytdl] extraction done  title={data.get('title')!r}", flush=True)

        stream_url: str = data["url"]
        print(f"[ytdl] creating FFmpegPCMAudio  url={stream_url[:60]}...", flush=True)
        audio_source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)
        print("[ytdl] FFmpegPCMAudio created OK", flush=True)
        return cls(audio_source, data=data)

    def formatted_duration(self) -> str:
        if self.duration is None:
            return "Unknown"
        minutes, seconds = divmod(int(self.duration), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"
