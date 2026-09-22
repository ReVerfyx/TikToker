from __future__ import annotations

import json
import logging
import random
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml
from tiktok_uploader.upload import TikTokUploader
from yt_dlp import YoutubeDL

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("tiktoker")


def cfg_load() -> dict[str, Any]:
    p = ROOT / "config.yaml"
    if not p.exists():
        raise SystemExit("Нет config.yaml: cp config.example.yaml config.yaml")
    return yaml.safe_load(p.read_text("utf-8"))


def pabs(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS videos(
          video_id TEXT PRIMARY KEY,
          title TEXT, channel TEXT, url TEXT,
          status TEXT NOT NULL DEFAULT 'found',
          parts INTEGER NOT NULL DEFAULT 0,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS uploads(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          video_id TEXT NOT NULL,
          part_no INTEGER NOT NULL,
          path TEXT NOT NULL,
          caption TEXT NOT NULL,
          account_name TEXT NOT NULL,
          cookie_path TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          tries INTEGER NOT NULL DEFAULT 0,
          last_error TEXT,
          UNIQUE(video_id, part_no)
        );
        CREATE TABLE IF NOT EXISTS state(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """)
        self.db.commit()

    def seen(self, video_id: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM videos WHERE video_id=? LIMIT 1", (video_id,)
        ).fetchone() is not None

    def add_video(self, info: dict[str, Any], status: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO videos(video_id,title,channel,url,status) VALUES(?,?,?,?,?)",
            (
                info["id"],
                info.get("title") or info["id"],
                info.get("channel") or info.get("uploader") or "",
                info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}",
                status,
            ),
        )
        self.db.commit()

    def video_status(self, video_id: str, status: str, parts: int | None = None) -> None:
        if parts is None:
            self.db.execute("UPDATE videos SET status=? WHERE video_id=?", (status, video_id))
        else:
            self.db.execute(
                "UPDATE videos SET status=?, parts=? WHERE video_id=?",
                (status, parts, video_id),
            )
        self.db.commit()

    def add_upload(
        self, video_id: str, part_no: int, path: Path, text: str,
        account_name: str, cookie_path: Path
    ) -> None:
        self.db.execute(
            """INSERT OR IGNORE INTO uploads
               (video_id,part_no,path,caption,account_name,cookie_path)
               VALUES(?,?,?,?,?,?)""",
            (video_id, part_no, str(path), text, account_name, str(cookie_path)),
        )
        self.db.commit()

    def pending(self) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT * FROM uploads WHERE status='pending' ORDER BY id"
        ))

    def upload_result(self, upload_id: int, ok: bool, error: str = "") -> None:
        self.db.execute(
            "UPDATE uploads SET status=?, tries=tries+1, last_error=? WHERE id=?",
            ("done" if ok else "pending", error or None, upload_id),
        )
        self.db.commit()

    def all_done(self, video_id: str) -> bool:
        r = self.db.execute(
            """SELECT COUNT(*) total,
               SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) done
               FROM uploads WHERE video_id=?""",
            (video_id,),
        ).fetchone()
        return bool(r and r["total"] and r["total"] == r["done"])

    def state_int(self, key: str, default: int = 0) -> int:
        r = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        try:
            return int(r["value"]) if r else default
        except Exception:
            return default

    def set_state(self, key: str, value: int) -> None:
        self.db.execute(
            """INSERT INTO state(key,value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, str(value)),
        )
        self.db.commit()


def youtube_cookie_path(cfg: dict[str, Any]) -> Path | None:
    raw = str(cfg.get("youtube", {}).get("cookies_file", "youtube_cookies.txt") or "").strip()
    if not raw:
        return None
    path = pabs(raw)
    return path if path.is_file() else None


def ydl_options(cfg: dict[str, Any]) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }

    cookie = youtube_cookie_path(cfg)
    if cookie:
        opts["cookiefile"] = str(cookie)

    sleep_requests = float(cfg.get("youtube", {}).get("sleep_requests", 2) or 0)
    if sleep_requests > 0:
        opts["sleep_interval_requests"] = sleep_requests

    return opts


def ydl_info(url: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    opts = ydl_options(cfg)
    opts["noplaylist"] = True
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def flat_list(url: str, limit: int, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    opts = ydl_options(cfg)
    opts["extract_flat"] = True
    opts["playlistend"] = limit

    with YoutubeDL(opts) as ydl:
        data = ydl.extract_info(url, download=False) or {}

    entries = data.get("entries") or []
    return [e for e in entries if e and e.get("id")]


def discover(cfg: dict[str, Any], db: DB) -> dict[str, Any] | None:
    yc = cfg["youtube"]
    mode = str(yc.get("source_mode", "both")).lower()
    limit = int(yc.get("search_results", 20))
    queries = [str(x).strip() for x in (yc.get("queries") or []) if str(x).strip()]
    channels = [str(x).strip() for x in (yc.get("channels") or []) if str(x).strip()]
    urls: list[str] = []

    if mode in ("search", "both") and queries:
        q = random.choice(queries)
        LOG.info("Поиск: %s", q)
        for e in flat_list(f"ytsearch{limit}:{q}", limit, cfg):
            urls.append(f"https://www.youtube.com/watch?v={e['id']}")

    if mode in ("channels", "both") and channels:
        ch = random.choice(channels).rstrip("/")
        LOG.info("Канал: %s", ch)
        for e in flat_list(ch + "/videos", limit, cfg):
            urls.append(f"https://www.youtube.com/watch?v={e['id']}")

    random.shuffle(urls)
    min_d = int(yc.get("min_duration_seconds", 0))
    max_d = int(yc.get("max_duration_seconds", 999999999))

    for url in urls:
        vid = url.rsplit("=", 1)[-1]
        if db.seen(vid):
            continue
        try:
            info = ydl_info(url, cfg)
            if not info:
                continue
            duration = int(info.get("duration") or 0)
            if min_d <= duration <= max_d:
                return info
        except Exception:
            LOG.exception("Ошибка чтения %s", url)
    return None


def download(info: dict[str, Any], work: Path, cfg: dict[str, Any]) -> Path:
    work.mkdir(parents=True, exist_ok=True)

    command = [
        str(ROOT / ".venv" / "bin" / "yt-dlp"),
        "--no-playlist",
        "--merge-output-format", "mp4",
        "-f", "bv*+ba/b",
        "-o", str(work / "source.%(ext)s"),
    ]

    cookie = youtube_cookie_path(cfg)
    if cookie:
        command += ["--cookies", str(cookie)]

    sleep_requests = float(cfg.get("youtube", {}).get("sleep_requests", 2) or 0)
    if sleep_requests > 0:
        command += ["--sleep-requests", str(sleep_requests)]

    command.append(
        info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}"
    )

    subprocess.run(command, check=True)

    files = sorted(work.glob("source.*"))
    if not files:
        raise RuntimeError("Исходный файл не найден после yt-dlp")
    return files[0]


def cut(source: Path, out_dir: Path, cfg: dict[str, Any]) -> list[Path]:
    pc = cfg["processing"]
    out_dir.mkdir(parents=True, exist_ok=True)
    seconds = max(1, int(pc.get("part_seconds", 300)))
    pattern = out_dir / "part_%03d.mp4"
    cmd = ["ffmpeg", "-y", "-i", str(source)]

    if pc.get("mode", "vertical_blur") == "vertical_blur":
        w = int(pc.get("width", 1080))
        h = int(pc.get("height", 1920))
        filt = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},gblur=sigma=30[bg2];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fg2];"
            f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2[v]"
        )
        cmd += ["-filter_complex", filt, "-map", "[v]", "-map", "0:a?"]
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a?"]

    cmd += [
        "-c:v", "libx264",
        "-preset", str(pc.get("preset", "veryfast")),
        "-crf", str(pc.get("crf", 23)),
        "-c:a", "aac",
        "-b:a", str(pc.get("audio_bitrate", "128k")),
        "-force_key_frames", f"expr:gte(t,n_forced*{seconds})",
        "-f", "segment",
        "-segment_time", str(seconds),
        "-reset_timestamps", "1",
        str(pattern),
    ]
    subprocess.run(cmd, check=True)
    parts = sorted(out_dir.glob("part_*.mp4"))
    if not parts:
        raise RuntimeError("FFmpeg не создал части")
    return parts


def local_tags(info: dict[str, Any], count: int) -> list[str]:
    text = " ".join([
        str(info.get("title") or ""),
        str(info.get("description") or "")[:1500],
        str(info.get("channel") or info.get("uploader") or ""),
    ]).lower()
    words = re.findall(r"[a-zа-яё0-9]{4,}", text, re.I)
    stop = {
        "https", "http", "youtube", "video", "видео", "канал", "часть",
        "этого", "этой", "который", "которые", "ссылка", "описание",
        "подписывайтесь", "смотреть"
    }
    freq: dict[str, int] = {}
    for word in words:
        if word in stop or word.isdigit():
            continue
        freq[word] = freq.get(word, 0) + 1
    return sorted(freq, key=lambda x: (-freq[x], len(x), x))[:count]



def make_caption(info: dict[str, Any], n: int, total: int, cfg: dict[str, Any]) -> str:
    cc = cfg.get("captions", {})
    auto_count = int(cc.get("max_auto_hashtags", 7))
    base = str(info.get("title") or "Видео").strip()

    required = cc.get(
        "required_hashtags",
        ["fyp", "рек", "рекомендации"],
    )
    auto = local_tags(info, auto_count)

    tags: list[str] = []
    for raw in [*required, *auto]:
        tag = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_]", "", str(raw).lstrip("#"))
        if tag and tag.lower() not in {x.lower() for x in tags}:
            tags.append(tag)

    lines = [base, f"Часть {n}/{total}"]
    if cc.get("source_credit", True):
        channel = info.get("channel") or info.get("uploader") or "автор"
        template = str(cc.get("source_template", "Источник: YouTube — {channel}"))
        lines.append(template.format(channel=channel))
    if tags:
        lines.append(" ".join("#" + x for x in tags))
    return "\\n\\n".join(lines)


def _has_session_cookie(path: Path) -> bool:
    try:
        text = path.read_text("utf-8", errors="ignore")
        names = {"sessionid", "sessionid_ss", "sid_tt"}
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\\t")
            if len(parts) >= 7 and parts[5] in names:
                return True
    except Exception:
        pass
    return False


def get_accounts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    tc = cfg.get("tiktok", {})
    result = []

    allowed_files = None
    registry_path = pabs(str(tc.get("accounts_registry", "data/accounts.json")))
    require_scan = bool(tc.get("require_account_scan", True))

    if registry_path.is_file():
        try:
            registry = json.loads(registry_path.read_text("utf-8"))
            allowed_files = {
                str(row.get("cookie_file"))
                for row in registry
                if row.get("enabled") is True and row.get("status") == "public"
            }
            LOG.info(
                "Реестр аккаунтов: разрешено публичных аккаунтов %s",
                len(allowed_files),
            )
        except Exception as exc:
            LOG.warning("Не удалось прочитать реестр аккаунтов: %s", exc)
            if require_scan:
                return []
    elif require_scan:
        LOG.warning(
            "Нет data/accounts.json. Сначала запусти: tiktoker-accounts scan"
        )
        return []

    if tc.get("auto_discover_cookies", True):
        cookie_dir = pabs(str(tc.get("cookies_dir", "cookies")))
        for i, cookie in enumerate(sorted(cookie_dir.glob("account*.txt")), start=1):
            if allowed_files is not None and cookie.name not in allowed_files:
                continue
            result.append({
                "name": cookie.stem,
                "cookies": cookie,
            })
        if result:
            return result

    for i, item in enumerate(tc.get("accounts", []), start=1):
        cookie = pabs(str(item.get("cookies") or ""))
        if not cookie.is_file():
            LOG.warning("Нет cookies, аккаунт пропущен: %s", cookie)
            continue
        if allowed_files is not None and cookie.name not in allowed_files:
            continue
        result.append({
            "name": str(item.get("name") or f"account{i}"),
            "cookies": cookie,
        })

    return result

def assign_accounts(
    db: DB, all_accounts: list[dict[str, Any]], count: int, strategy: str
) -> list[dict[str, Any]]:
    if not all_accounts:
        raise RuntimeError("Нет ни одного рабочего cookies-файла")

    start = db.state_int("account_cursor", 0) % len(all_accounts)
    if strategy == "per_part":
        chosen = [all_accounts[(start + i) % len(all_accounts)] for i in range(count)]
        db.set_state("account_cursor", (start + count) % len(all_accounts))
        return chosen

    chosen = [all_accounts[start]] * count
    db.set_state("account_cursor", (start + 1) % len(all_accounts))
    return chosen


def queue_video(info: dict[str, Any], cfg: dict[str, Any], db: DB) -> None:
    vid = info["id"]
    folder = pabs(cfg["storage"]["work_dir"]) / vid
    source = download(info, folder, cfg)
    db.video_status(vid, "splitting")

    parts = cut(source, folder / "parts", cfg)
    all_accounts = get_accounts(cfg)
    strategy = str(cfg.get("tiktok", {}).get("account_strategy", "per_video"))
    assigned = assign_accounts(db, all_accounts, len(parts), strategy)

    for n, (part, account) in enumerate(zip(parts, assigned), start=1):
        db.add_upload(
            vid, n, part, make_caption(info, n, len(parts), cfg),
            account["name"], account["cookies"]
        )

    db.video_status(vid, "queued", len(parts))
    if source.exists():
        source.unlink()


def upload_one(row: sqlite3.Row, cfg: dict[str, Any]) -> None:
    path = Path(row["path"])
    cookie = Path(row["cookie_path"])
    if not path.is_file():
        raise RuntimeError(f"Нет файла: {path}")
    if not cookie.is_file():
        raise RuntimeError(f"Нет cookies: {cookie}")

    tc = cfg.get("tiktok", {})
    uploader = TikTokUploader(
        cookies=str(cookie),
        browser=str(tc.get("browser", "chromium")),
        headless=bool(tc.get("headless", True)),
    )
    uploader.upload_video(str(path), description=str(row["caption"]))


def process_uploads(cfg: dict[str, Any], db: DB) -> None:
    delay = max(0, int(cfg.get("tiktok", {}).get("upload_delay_seconds", 60)))

    for row in db.pending():
        LOG.info(
            "TikTok: %s part=%s account=%s",
            row["video_id"], row["part_no"], row["account_name"]
        )
        try:
            upload_one(row, cfg)
            db.upload_result(row["id"], True)
            path = Path(row["path"])
            if cfg.get("storage", {}).get("delete_after_success", True) and path.exists():
                path.unlink()

            if db.all_done(row["video_id"]):
                db.video_status(row["video_id"], "done")
                if cfg.get("storage", {}).get("delete_after_success", True):
                    shutil.rmtree(
                        pabs(cfg["storage"]["work_dir"]) / row["video_id"],
                        ignore_errors=True,
                    )

            if delay:
                time.sleep(delay)
        except Exception as exc:
            LOG.exception("Ошибка публикации")
            db.upload_result(row["id"], False, str(exc))
            break


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )
    cfg = cfg_load()
    db = DB(pabs(cfg["storage"]["database"]))
    interval = max(60, int(cfg["youtube"].get("check_every_seconds", 3600)))

    LOG.info("TikToker запущен. Аккаунты динамические: лимита в коде нет.")

    while True:
        try:
            process_uploads(cfg, db)
            if not db.pending():
                info = discover(cfg, db)
                if info:
                    LOG.info("Найдено: %s | %s", info.get("channel"), info.get("title"))
                    db.add_video(info, "found")
                    queue_video(info, cfg, db)
                    process_uploads(cfg, db)
                else:
                    LOG.info("Новых видео нет")
        except KeyboardInterrupt:
            raise
        except Exception:
            LOG.exception("Ошибка цикла")

        LOG.info("Следующая проверка через %s сек.", interval)
        time.sleep(interval)


if __name__ == "__main__":
    main()
