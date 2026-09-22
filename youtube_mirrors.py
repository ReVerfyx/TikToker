from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

LOG = logging.getLogger("tiktoker.mirrors")

DEFAULT_INVIDIOUS = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yt.chocolatemoo53.com",
    "https://invidious.tiekoetter.com",
    "https://invidious.f5.si",
]

DEFAULT_PIPED = [
    "https://pipedapi.ducks.party",
    "https://api.piped.private.coffee",
    "https://pipedapi.qwik.space",
]

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)


def _mirror_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("youtube", {}).get("mirror_fallback", {}) or {}


def enabled(cfg: dict[str, Any]) -> bool:
    return bool(_mirror_cfg(cfg).get("enabled", True))


def _instances(cfg: dict[str, Any], key: str, default: list[str]) -> list[str]:
    value = _mirror_cfg(cfg).get(key)
    if not value:
        value = default
    return [str(x).rstrip("/") for x in value if str(x).strip()]


def _timeout(cfg: dict[str, Any]) -> float:
    return float(_mirror_cfg(cfg).get("timeout_seconds", 20) or 20)


def _max_height(cfg: dict[str, Any]) -> int:
    return int(_mirror_cfg(cfg).get("max_height", 720) or 720)


def _json_get(
    base: str,
    path: str,
    cfg: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> Any:
    url = base.rstrip("/") + path
    if params:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )
        url += ("&" if "?" in url else "?") + query

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=_timeout(cfg)) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


def _video_id(value: str) -> str | None:
    value = str(value or "")
    match = re.search(r"(?:v=|/watch\?v=)([A-Za-z0-9_-]{11})", value)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    return None


def _height(value: Any) -> int:
    if isinstance(value, int):
        return value
    match = re.search(r"(\d{3,4})", str(value or ""))
    return int(match.group(1)) if match else 0


def _within_height(height: int, maximum: int) -> int:
    if height <= 0:
        return -1
    if height <= maximum:
        return height + 100000
    return -height


def search(query: str, limit: int, cfg: dict[str, Any]) -> list[str]:
    if not enabled(cfg):
        return []

    for base in _instances(cfg, "invidious_instances", DEFAULT_INVIDIOUS):
        try:
            data = _json_get(
                base,
                "/api/v1/search",
                cfg,
                {
                    "q": query,
                    "type": "video",
                    "sort_by": "relevance",
                },
            )
            ids: list[str] = []
            for item in data if isinstance(data, list) else []:
                if item.get("type") not in ("video", None):
                    continue
                vid = _video_id(item.get("videoId") or "")
                if vid and vid not in ids:
                    ids.append(vid)
                if len(ids) >= limit:
                    break
            if ids:
                LOG.info("Invidious поиск через %s: %s результатов", base, len(ids))
                return ids
        except Exception as exc:
            LOG.warning("Invidious %s недоступен для поиска: %s", base, exc)

    for base in _instances(cfg, "piped_instances", DEFAULT_PIPED):
        try:
            data = _json_get(
                base,
                "/search",
                cfg,
                {
                    "q": query,
                    "filter": "videos",
                },
            )
            if isinstance(data, dict):
                items = (
                    data.get("items")
                    or data.get("results")
                    or data.get("relatedStreams")
                    or []
                )
            elif isinstance(data, list):
                items = data
            else:
                items = []

            ids: list[str] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                vid = _video_id(
                    item.get("url")
                    or item.get("videoId")
                    or item.get("id")
                    or ""
                )
                if vid and vid not in ids:
                    ids.append(vid)
                if len(ids) >= limit:
                    break
            if ids:
                LOG.info("Piped поиск через %s: %s результатов", base, len(ids))
                return ids
        except Exception as exc:
            LOG.warning("Piped %s недоступен для поиска: %s", base, exc)

    return []


def video_info(video_id: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    if not enabled(cfg):
        return None

    for base in _instances(cfg, "invidious_instances", DEFAULT_INVIDIOUS):
        try:
            data = _json_get(
                base,
                f"/api/v1/videos/{video_id}",
                cfg,
                {"local": "true"},
            )

            duration = int(data.get("lengthSeconds") or 0)
            if not data.get("title"):
                continue

            return {
                "id": video_id,
                "title": str(data.get("title") or video_id),
                "description": str(data.get("description") or ""),
                "duration": duration,
                "channel": str(data.get("author") or ""),
                "uploader": str(data.get("author") or ""),
                "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                "_mirror_provider": "invidious",
                "_mirror_base": base,
                "_mirror_raw": data,
            }
        except Exception as exc:
            LOG.warning("Invidious %s не дал видео %s: %s", base, video_id, exc)

    for base in _instances(cfg, "piped_instances", DEFAULT_PIPED):
        try:
            data = _json_get(base, f"/streams/{video_id}", cfg)

            if not data.get("title"):
                continue

            return {
                "id": video_id,
                "title": str(data.get("title") or video_id),
                "description": str(data.get("description") or ""),
                "duration": int(data.get("duration") or 0),
                "channel": str(data.get("uploader") or ""),
                "uploader": str(data.get("uploader") or ""),
                "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                "_mirror_provider": "piped",
                "_mirror_base": base,
                "_mirror_raw": data,
            }
        except Exception as exc:
            LOG.warning("Piped %s не дал видео %s: %s", base, video_id, exc)

    return None


def _ffmpeg_progressive(url: str, target: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "warning",
            "-user_agent",
            UA,
            "-i",
            url,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
            str(target),
        ],
        check=True,
    )


def _ffmpeg_separate(video_url: str, audio_url: str, target: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "warning",
            "-user_agent",
            UA,
            "-i",
            video_url,
            "-user_agent",
            UA,
            "-i",
            audio_url,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            str(target),
        ],
        check=True,
    )


def _piped_video_info_only(
    video_id: str,
    cfg: dict[str, Any],
    skip_base: str = "",
) -> dict[str, Any] | None:
    for base in _instances(cfg, "piped_instances", DEFAULT_PIPED):
        if base == skip_base:
            continue
        try:
            data = _json_get(base, f"/streams/{video_id}", cfg)
            if not data.get("title"):
                continue
            return {
                "id": video_id,
                "title": str(data.get("title") or video_id),
                "description": str(data.get("description") or ""),
                "duration": int(data.get("duration") or 0),
                "channel": str(data.get("uploader") or ""),
                "uploader": str(data.get("uploader") or ""),
                "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                "_mirror_provider": "piped",
                "_mirror_base": base,
                "_mirror_raw": data,
            }
        except Exception as exc:
            LOG.warning("Piped %s fallback не дал видео %s: %s", base, video_id, exc)
    return None


def _download_invidious(info: dict[str, Any], work: Path, cfg: dict[str, Any]) -> Path:
    raw = info.get("_mirror_raw") or {}
    base = str(info.get("_mirror_base") or "").rstrip("/")
    video_id = str(info["id"])
    maximum = _max_height(cfg)

    formats = [
        item
        for item in (raw.get("formatStreams") or [])
        if isinstance(item, dict)
    ]

    mp4 = [
        item
        for item in formats
        if str(item.get("container") or "").lower() in ("mp4", "")
    ]
    if mp4:
        formats = mp4

    formats.sort(
        key=lambda item: _within_height(
            _height(item.get("qualityLabel") or item.get("resolution")),
            maximum,
        ),
        reverse=True,
    )

    for item in formats:
        itag = str(item.get("itag") or "").strip()
        if not itag:
            continue

        url = (
            f"{base}/latest_version?"
            + urllib.parse.urlencode(
                {
                    "id": video_id,
                    "itag": itag,
                    "local": "true",
                }
            )
        )

        target = work / "source.mkv"
        try:
            _ffmpeg_progressive(url, target)
            if target.is_file() and target.stat().st_size > 1024:
                LOG.info("Видео скачано через Invidious: %s", base)
                return target
        except Exception as exc:
            LOG.warning(
                "Invidious поток %s itag=%s не скачался: %s",
                base,
                itag,
                exc,
            )
            target.unlink(missing_ok=True)

    raise RuntimeError(f"Invidious {base} не дал скачиваемый progressive-поток")


def _download_piped(info: dict[str, Any], work: Path, cfg: dict[str, Any]) -> Path:
    raw = info.get("_mirror_raw") or {}
    base = str(info.get("_mirror_base") or "")
    maximum = _max_height(cfg)

    video_streams = [
        item
        for item in (raw.get("videoStreams") or [])
        if isinstance(item, dict) and item.get("url")
    ]

    progressive = [
        item
        for item in video_streams
        if not bool(item.get("videoOnly"))
        and (
            "mp4" in str(item.get("mimeType") or "").lower()
            or str(item.get("format") or "").upper() == "MPEG_4"
        )
    ]

    progressive.sort(
        key=lambda item: _within_height(
            int(item.get("height") or _height(item.get("quality")) or 0),
            maximum,
        ),
        reverse=True,
    )

    target = work / "source.mkv"

    for item in progressive:
        try:
            _ffmpeg_progressive(str(item["url"]), target)
            if target.is_file() and target.stat().st_size > 1024:
                LOG.info("Видео скачано через Piped: %s", base)
                return target
        except Exception as exc:
            LOG.warning("Piped progressive через %s не скачался: %s", base, exc)
            target.unlink(missing_ok=True)

    separate_video = [
        item
        for item in video_streams
        if bool(item.get("videoOnly"))
        and (
            "mp4" in str(item.get("mimeType") or "").lower()
            or str(item.get("format") or "").upper() == "MPEG_4"
        )
    ]

    separate_video.sort(
        key=lambda item: _within_height(
            int(item.get("height") or _height(item.get("quality")) or 0),
            maximum,
        ),
        reverse=True,
    )

    audio_streams = [
        item
        for item in (raw.get("audioStreams") or [])
        if isinstance(item, dict) and item.get("url")
    ]
    audio_streams.sort(
        key=lambda item: int(item.get("bitrate") or 0),
        reverse=True,
    )

    if separate_video and audio_streams:
        try:
            _ffmpeg_separate(
                str(separate_video[0]["url"]),
                str(audio_streams[0]["url"]),
                target,
            )
            if target.is_file() and target.stat().st_size > 1024:
                LOG.info("Видео+аудио скачаны через Piped: %s", base)
                return target
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"Piped {base}: {exc}") from exc

    raise RuntimeError(f"Piped {base} не дал скачиваемый поток")


def download(info: dict[str, Any], work: Path, cfg: dict[str, Any]) -> Path:
    provider = str(info.get("_mirror_provider") or "")

    work.mkdir(parents=True, exist_ok=True)

    if provider == "invidious":
        try:
            return _download_invidious(info, work, cfg)
        except Exception as first_error:
            LOG.warning(
                "Invidious дал метаданные, но поток не скачался: %s. "
                "Переключаюсь на Piped.",
                first_error,
            )
            piped = _piped_video_info_only(str(info["id"]), cfg)
            if piped:
                return _download_piped(piped, work, cfg)
            raise

    if provider == "piped":
        try:
            return _download_piped(info, work, cfg)
        except Exception as first_error:
            LOG.warning("Piped %s не скачал поток: %s", info.get("_mirror_base"), first_error)
            next_piped = _piped_video_info_only(
                str(info["id"]),
                cfg,
                skip_base=str(info.get("_mirror_base") or ""),
            )
            if next_piped:
                return _download_piped(next_piped, work, cfg)
            raise

    raise RuntimeError("Неизвестный mirror provider")
