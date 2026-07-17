import asyncio
import functools
import yt_dlp

# Suppress yt-dlp output noise
yt_dlp.utils.bug_reports_message = lambda: ""

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


class YTDLSource:
    """Wraps a yt-dlp result into a discord.FFmpegPCMAudio source."""

    def __init__(self, source, *, data: dict, volume: float = 0.5):
        self.source = source
        self.data = data
        self.title = data.get("title", "Unknown")
        self.url = data.get("webpage_url", "")
        self.duration = data.get("duration")
        self.thumbnail = data.get("thumbnail")
        self.uploader = data.get("uploader", "Unknown")

    @classmethod
    async def from_query(cls, query: str, *, loop: asyncio.AbstractEventLoop = None):
        """
        Search or resolve a YouTube URL/query and return a YTDLSource.
        Runs the blocking yt-dlp call in an executor.
        """
        loop = loop or asyncio.get_event_loop()

        # If not a URL, treat as a search query
        if not query.startswith(("http://", "https://")):
            query = f"ytsearch:{query}"

        partial = functools.partial(
            ytdl.extract_info, query, download=False
        )
        data = await loop.run_in_executor(None, partial)

        # ytsearch returns a dict with an "entries" list
        if "entries" in data:
            if not data["entries"]:
                raise ValueError("No results found for that query.")
            data = data["entries"][0]

        stream_url = data["url"]
        source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)
        return cls(source, data=data)

    def formatted_duration(self) -> str:
        if self.duration is None:
            return "Unknown"
        minutes, seconds = divmod(int(self.duration), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"
