#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
COOKIE_DIR = ROOT / "cookies"
DATA_DIR = ROOT / "data"
REGISTRY_JSON = DATA_DIR / "accounts.json"
REGISTRY_CSV = DATA_DIR / "accounts.csv"
REGISTRY_TXT = DATA_DIR / "accounts.txt"
IMPORT_MAP = DATA_DIR / "import_map.json"

PROFILE_SELECTORS = [
    'a[data-e2e="nav-profile"]',
    'a[data-e2e="profile-icon"]',
    'a[aria-label="Profile"]',
    'a[aria-label="Профиль"]',
]

PRIVATE_TEXT = (
    "this account is private",
    "account is private",
    "этот аккаунт приватный",
    "это приватный аккаунт",
    "закрытый аккаунт",
)


def read_import_map() -> dict[str, dict]:
    try:
        data = json.loads(IMPORT_MAP.read_text("utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def parse_netscape(path: Path) -> list[dict]:
    cookies = []
    now = int(time.time())

    for raw in path.read_text("utf-8", errors="ignore").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue

        parts = raw.split("\t")
        if len(parts) < 7:
            continue

        domain, _, cookie_path, secure, expires, name, value = parts[:7]

        item = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": cookie_path or "/",
            "secure": secure.upper() == "TRUE",
        }

        try:
            expiry = int(float(expires))
            if expiry > now:
                item["expires"] = expiry
        except Exception:
            pass

        cookies.append(item)

    return cookies


def username_from_href(href: str | None) -> str | None:
    if not href:
        return None
    match = re.search(r"/@([^/?#]+)", href)
    return urllib.parse.unquote(match.group(1)) if match else None


def find_profile_href(page) -> str | None:
    for selector in PROFILE_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() < 1:
                continue

            href = locator.get_attribute("href")
            if href:
                return href

            try:
                parent = locator.locator("xpath=ancestor::a[1]")
                href = parent.get_attribute("href")
                if href:
                    return href
            except Exception:
                pass
        except Exception:
            continue

    return None


def username_from_html_and_id(html: str, user_id: str | None) -> str | None:
    if not user_id or not user_id.isdigit():
        return None

    escaped = re.escape(user_id)
    patterns = [
        rf'"id":"{escaped}".{{0,6000}}"uniqueId":"([^"]+)"',
        rf'"uniqueId":"([^"]+)".{{0,6000}}"id":"{escaped}"',
        rf'"uid":"{escaped}".{{0,6000}}"uniqueId":"([^"]+)"',
        rf'"uniqueId":"([^"]+)".{{0,6000}}"uid":"{escaped}"',
    ]

    for pattern in patterns:
        match = re.search(pattern, html, flags=re.S)
        if match:
            return match.group(1)

    return None


def profile_private_from_html(html: str, username: str) -> bool | None:
    escaped_user = re.escape(username)

    patterns = [
        rf'"uniqueId":"{escaped_user}".{{0,8000}}"privateAccount":(true|false)',
        rf'"privateAccount":(true|false).{{0,8000}}"uniqueId":"{escaped_user}"',
    ]

    for pattern in patterns:
        match = re.search(pattern, html, flags=re.S | re.I)
        if match:
            return match.group(1).lower() == "true"

    lower = html.lower()
    if any(text in lower for text in PRIVATE_TEXT):
        return True

    return None


def oembed_public(username: str) -> bool | None:
    url = (
        "https://www.tiktok.com/oembed?url="
        + urllib.parse.quote(f"https://www.tiktok.com/@{username}", safe="")
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                return False
            data = json.loads(response.read().decode("utf-8", errors="replace"))
            return bool(data.get("author_url") or data.get("html"))
    except urllib.error.HTTPError:
        return False
    except Exception:
        return None


def classify_profile(page, username: str) -> tuple[str, str]:
    profile_url = f"https://www.tiktok.com/@{username}"

    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1200)
    except PlaywrightTimeoutError:
        pass

    html = page.content()
    private = profile_private_from_html(html, username)

    if private is True:
        return "private", "Профиль определён как приватный"

    if private is False:
        return "public", "Профиль определён как публичный"

    embedded = oembed_public(username)

    if embedded is True:
        return "public", "Публичный профиль подтверждён через TikTok oEmbed"

    if embedded is False:
        return (
            "private_or_unavailable",
            "Профиль не отдаётся через oEmbed: приватный, возрастной, удалённый или недоступный",
        )

    return "unknown", "Не удалось надёжно определить публичность"


def scan_one(browser, cookie_path: Path, source_meta: dict) -> dict:
    result = {
        "cookie_file": cookie_path.name,
        "source_file": source_meta.get("source_file", ""),
        "source_user_id": source_meta.get("source_user_id", ""),
        "username": "",
        "profile_url": "",
        "privacy": "unknown",
        "status": "unknown",
        "enabled": False,
        "note": "",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    cookies = parse_netscape(cookie_path)
    if not cookies:
        result["status"] = "invalid"
        result["note"] = "Cookie-файл пуст или не Netscape"
        return result

    context = browser.new_context(
        locale="ru-RU",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
    )

    try:
        context.add_cookies(cookies)
        page = context.new_page()

        try:
            page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
        except PlaywrightTimeoutError:
            pass

        if "/login" in page.url:
            result["status"] = "invalid"
            result["note"] = "TikTok перенаправил на страницу входа"
            return result

        href = find_profile_href(page)
        username = username_from_href(href)

        if not username:
            html = page.content()
            username = username_from_html_and_id(
                html,
                str(source_meta.get("source_user_id") or ""),
            )

        if not username:
            result["status"] = "unknown"
            result["note"] = "Сессия открылась, но @username не удалось извлечь"
            return result

        result["username"] = username
        result["profile_url"] = f"https://www.tiktok.com/@{username}"

        privacy, note = classify_profile(page, username)
        result["privacy"] = privacy
        result["note"] = note

        if privacy == "public":
            result["status"] = "public"
            result["enabled"] = True
        elif privacy == "private":
            result["status"] = "private"
        elif privacy == "private_or_unavailable":
            result["status"] = "private_or_unavailable"
        else:
            result["status"] = "unknown"

        return result

    except Exception as exc:
        result["status"] = "error"
        result["note"] = f"{type(exc).__name__}: {exc}"[:500]
        return result

    finally:
        context.close()


def write_registry(rows: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    REGISTRY_JSON.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    columns = [
        "cookie_file",
        "source_file",
        "source_user_id",
        "username",
        "profile_url",
        "privacy",
        "status",
        "enabled",
        "note",
        "checked_at",
    ]

    with REGISTRY_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    lines = []
    for row in rows:
        username = f"@{row['username']}" if row.get("username") else "(username неизвестен)"
        enabled = "ACTIVE" if row.get("enabled") else "SKIP"
        lines.append(
            f"{row['cookie_file']} | {username} | {row['status']} | {enabled}"
        )

    REGISTRY_TXT.write_text("\n".join(lines) + ("\n" if lines else ""), "utf-8")


def registry_fresh(hours: float) -> bool:
    if not REGISTRY_JSON.exists():
        return False
    age = time.time() - REGISTRY_JSON.stat().st_mtime
    return age < hours * 3600


def scan() -> list[dict]:
    files = sorted(COOKIE_DIR.glob("account*.txt"))
    import_map = read_import_map()

    if not files:
        write_registry([])
        print("Cookie-аккаунты не найдены.")
        return []

    print(f"Проверяю аккаунты: {len(files)}")

    rows = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        try:
            for index, path in enumerate(files, start=1):
                print(f"[{index}/{len(files)}] {path.name}")
                meta = dict(import_map.get(path.name, {}))
                if not meta.get("source_user_id"):
                    match = re.fullmatch(r"account_(\d+)", path.stem)
                    if match:
                        meta["source_user_id"] = match.group(1)
                        meta["source_file"] = path.name
                row = scan_one(browser, path, meta)
                rows.append(row)

                username = f"@{row['username']}" if row.get("username") else "-"
                print(
                    f"    {username} | {row['status']} | "
                    + ("используется" if row["enabled"] else "исключён")
                )
        finally:
            browser.close()

    write_registry(rows)

    active = sum(1 for row in rows if row["enabled"])
    private = sum(
        1
        for row in rows
        if row["status"] in {"private", "private_or_unavailable"}
    )

    print()
    print(f"Всего: {len(rows)}")
    print(f"Публичных/активных: {active}")
    print(f"Приватных или недоступных: {private}")
    print(f"Список: {REGISTRY_TXT}")
    print(f"CSV: {REGISTRY_CSV}")

    return rows


def show() -> None:
    if not REGISTRY_JSON.exists():
        print("Список ещё не создан. Запусти: tiktoker-accounts scan")
        return

    rows = json.loads(REGISTRY_JSON.read_text("utf-8"))
    if not rows:
        print("Аккаунтов нет.")
        return

    print(f"{'COOKIE':<18} {'USERNAME':<28} {'STATUS':<24} USE")
    print("-" * 84)

    for row in rows:
        username = f"@{row['username']}" if row.get("username") else "-"
        use = "YES" if row.get("enabled") else "NO"
        print(
            f"{row['cookie_file']:<18} "
            f"{username[:27]:<28} "
            f"{row['status'][:23]:<24} "
            f"{use}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scan")
    sub.add_parser("show")

    stale = sub.add_parser("refresh-if-stale")
    stale.add_argument("hours", type=float, nargs="?", default=24)

    args = parser.parse_args()

    if args.command == "show":
        show()
        return

    if args.command == "refresh-if-stale":
        if registry_fresh(args.hours):
            print("Реестр аккаунтов свежий, повторная проверка не нужна.")
            return
        scan()
        return

    scan()


if __name__ == "__main__":
    main()
