import hashlib
from pathlib import Path
from tiktok_uploader.upload import TikTokUploader


def account_for(video_id: str, accounts: list):
    if not accounts:
        raise RuntimeError("В config.yaml нет TikTok-аккаунтов")
    idx = int(hashlib.sha256(video_id.encode()).hexdigest()[:8], 16) % len(accounts)
    return accounts[idx]


def caption(row):
    channel = (row["channel"] or "автор").strip()
    return (
        f'{row["title"]}\n'
        f'Часть {row["part_no"]}/{row["total_parts"]}\n'
        f'Источник: YouTube — {channel}\n\n'
        f'{row["hashtags"]}'
    )


def upload_one(row, cfg):
    accounts = cfg.get("accounts", [])
    account = account_for(row["video_id"], accounts)
    path = Path(row["part_path"])
    if not path.exists():
        raise FileNotFoundError(path)

    if cfg.get("dry_run", False):
        print(f"[DRY-RUN] {account['name']}: {path}")
        return account["name"]

    uploader = TikTokUploader(cookies=account["cookies"], headless=True)
    uploader.upload_video(str(path), description=caption(row))
    return account["name"]
