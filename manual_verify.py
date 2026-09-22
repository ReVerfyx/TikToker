#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from accounts import parse_netscape
from comment import captcha_detected, resolve_account

ROOT = Path(__file__).resolve().parent
BACKUP_DIR = ROOT / "data" / "cookie_backups"


def write_netscape(path: Path, cookies: list[dict]) -> None:
    lines = [
        "# Netscape HTTP Cookie File",
        "# Saved by TikToker manual verification",
    ]

    for c in cookies:
        domain = str(c.get("domain") or "")
        if not domain:
            continue

        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        cookie_path = str(c.get("path") or "/")
        secure = "TRUE" if c.get("secure") else "FALSE"

        expires = c.get("expires")
        try:
            expires_int = int(float(expires)) if expires not in (None, -1) else 0
        except Exception:
            expires_int = 0

        name = str(c.get("name") or "")
        value = str(c.get("value") or "")
        if not name:
            continue

        lines.append(
            "\t".join(
                [
                    domain,
                    include_subdomains,
                    cookie_path,
                    secure,
                    str(expires_int),
                    name,
                    value,
                ]
            )
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_updated_cookies(context, cookie_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{cookie_path.stem}_{stamp}.txt"

    shutil.copy2(cookie_path, backup)

    cookies = [
        c for c in context.cookies()
        if "tiktok.com" in str(c.get("domain") or "").lower()
    ]

    if not cookies:
        raise RuntimeError("После проверки TikTok cookies не найдены.")

    write_netscape(cookie_path, cookies)
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ручная проверка TikTok CAPTCHA в видимом Chromium."
    )
    parser.add_argument("--account", required=True)
    parser.add_argument(
        "--url",
        default="https://www.tiktok.com/",
        help="Страница TikTok, которую открыть для ручной проверки.",
    )
    args = parser.parse_args()

    if not os.environ.get("DISPLAY"):
        raise SystemExit(
            "Нет DISPLAY. Запусти эту команду в Терминале внутри RDP-рабочего стола, "
            "а не через обычный SSH/Termius."
        )

    account = resolve_account(args.account)
    cookie_path = Path(account["cookie_path"])
    cookies = parse_netscape(cookie_path)

    if not cookies:
        raise SystemExit(f"Cookie-файл пуст: {cookie_path}")

    username = str(account.get("username") or args.account)
    print(f"Аккаунт: @{username}")
    print(f"Открываю: {args.url}")
    print("Chromium будет видимым. Не закрывай его вручную до сохранения cookies.")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            timeout=20000,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = browser.new_context(
            locale="ru-RU",
            viewport={"width": 1440, "height": 900},
            screen={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )
        context.add_cookies(cookies)
        page = context.new_page()

        try:
            try:
                page.goto(
                    args.url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except PlaywrightTimeoutError:
                pass

            page.wait_for_timeout(2000)

            print()
            print("=== ЧТО ДЕЛАТЬ НА ТЕЛЕФОНЕ ===")
            print("1. Переключись обратно в окно RDP.")
            print("2. В Chromium открой комментарии/действие, которое вызывает проверку.")
            print("3. Если появился пазл TikTok — реши его пальцем через RDP.")
            print("4. Когда проверка исчезнет, вернись в этот терминал.")
            print("5. Нажми Enter.")
            print()

            while True:
                input("Нажми Enter после ручного решения CAPTCHA... ")

                page.wait_for_timeout(1000)

                if captcha_detected(page):
                    print(
                        "CAPTCHA всё ещё видна. Реши пазл до конца и снова нажми Enter."
                    )
                    continue

                print("CAPTCHA на странице не обнаружена.")
                break

            backup = save_updated_cookies(context, cookie_path)
            print(f"Cookies обновлены: {cookie_path}")
            print(f"Резервная копия старых cookies: {backup}")
            print("Ручная проверка завершена.")

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
