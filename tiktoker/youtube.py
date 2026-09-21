import random
from pathlib import Path
import yt_dlp


class YouTubeSource:
    def __init__(self, cfg):
        self.cfg = cfg

    def _opts(self):
        opts = {"quiet": True, "no_warnings": True}
        if self.cfg.get("cookies_file"):
            opts["cookiefile"] = self.cfg["cookies_file"]
        return opts

    def _full(self, video_id):
        with yt_dlp.YoutubeDL({**self._opts(), "noplaylist": True}) as ydl:
            return ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )

    def discover(self, db):
        channels = [x for x in self.cfg.get("channels", []) if x]
        queries = [x for x in self.cfg.get("queries", []) if x]
        if not channels and not queries:
            return None

        use_channel = bool(channels) and (
            not queries
            or random.randint(1, 100) <= int(self.cfg.get("channel_probability", 70))
        )
        count = int(self.cfg.get("search_results", 12))

        if use_channel:
            target = random.choice(channels)
            opts = {**self._opts(), "extract_flat": True, "playlistend": count}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
            entries = info.get("entries") or []
        else:
            q = random.choice(queries)
            opts = {**self._opts(), "extract_flat": True, "noplaylist": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{count}:{q}", download=False)
            entries = info.get("entries") or []

        random.shuffle(entries)
        min_d = int(self.cfg.get("min_duration_seconds", 0))
        max_d = int(self.cfg.get("max_duration_seconds", 999999))

        for e in entries:
            vid = e.get("id") if e else None
            if not vid or db.is_processed(vid):
                continue
            try:
                full = self._full(vid)
            except Exception:
                continue
            dur = int(full.get("duration") or 0)
            if min_d <= dur <= max_d:
                return full
        return None

    def download(self, info, work_dir: Path):
        folder = work_dir / info["id"]
        folder.mkdir(parents=True, exist_ok=True)
        out = str(folder / "source.%(ext)s")
        opts = {
            **self._opts(),
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": out,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([info["webpage_url"]])

        files = [
            p for p in folder.iterdir()
            if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
        ]
        if not files:
            raise RuntimeError("Видео не скачалось")
        return max(files, key=lambda p: p.stat().st_size)
