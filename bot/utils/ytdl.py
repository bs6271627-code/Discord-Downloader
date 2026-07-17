import asyncio
import discord
import yt_dlp

# Suppress yt-dlp noise
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
    async def from_query(
        cls, query: str, *, loop: asyncio.AbstractEventLoop | None = None
    ) -> "YTDLSource":
        """
        Resolve a YouTube URL or search query and return a YTDLSource.
        The blocking yt-dlp call runs in a thread-pool executor.
        """
        loop = loop or asyncio.get_event_loop()

        if not query.startswith(("http://", "https://")):
            query = f"ytsearch:{query}"

        # Use a proper function (not functools.partial / lambda) so that
        # yt-dlp can call its internal hooks with arbitrary *args/**kwargs
        # without raising "unexpected keyword argument" errors.
        def _extract() -> dict:
            with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
                info = ydl.extract_info(query, download=False)
                if info is None:
                    raise ValueError("yt-dlp returned no data for that query.")
                # ytsearch wraps results in an "entries" list
                if "entries" in info:
                    entries = [e for e in info["entries"] if e]
                    if not entries:
                        raise ValueError("No results found for that query.")
                    info = entries[0]
                return ydl.sanitize_info(info)

        data = await loop.run_in_executor(None, _extract)

        stream_url: str = data["url"]
        audio_source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)
        return cls(audio_source, data=data)

    def formatted_duration(self) -> str:
        if self.duration is None:
            return "Unknown"
        minutes, seconds = divmod(int(self.duration), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"
