#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from accounts import parse_netscape
from comment import captcha_detected, resolve_account

ROOT = Path(__file__).resolve().parent
BACKUP_DIR = ROOT / "data" / "cookie_backups"
PENDING = ROOT / "data" / "captcha_pending.json"


def detect_rdp_display() -> tuple[str, str | None]:
    current = os.environ.get("DISPLAY")
    if current:
        return current, None

    try:
        output = subprocess.check_output(
            ["ps", "-eo", "user=,args="],
            text=True,
            errors="ignore",
        )
    except Exception:
        output = ""

    candidates: list[tuple[int, str]] = []

    for line in output.splitlines():
        if "Xorg" not in line or "xrdp" not in line.lower():
            continue

        match = re.search(r"\s:(\d+)(?:\s|$)", line)
        if not match:
            continue

        user = line.strip().split(None, 1)[0]
        candidates.append((int(match.group(1)), user))

    if not candidates:
        raise SystemExit(
            "Активная RDP-сессия не найдена.\n"
            "Открой aRDP, дождись рабочего стола Ubuntu, затем вернись "
            "в Termius и снова выполни: tiktoker-verify pending"
        )

    number, user = sorted(candidates, reverse=True)[0]
    display = f":{number}"

    os.environ["DISPLAY"] = display

    try:
        home = Path(pwd.getpwnam(user).pw_dir)
        xauth = home / ".Xauthority"
        if xauth.is_file():
            os.environ["XAUTHORITY"] = str(xauth)
    except Exception:
        pass

    return display, user


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
        c
        for c in context.cookies()
        if "tiktok.com" in str(c.get("domain") or "").lower()
    ]

    if not cookies:
        raise RuntimeError("После проверки TikTok cookies не найдены.")

    write_netscape(cookie_path, cookies)
    return backup


def load_pending() -> dict:
    if not PENDING.is_file():
        raise SystemExit(
            "Нет ожидающей CAPTCHA. Сначала обычный tiktoker-comment должен "
            "обнаружить CAPTCHA."
        )

    try:
        data = json.loads(PENDING.read_text("utf-8"))
    except Exception as exc:
        raise SystemExit(f"Не удалось прочитать {PENDING}: {exc}")

    if not isinstance(data, dict) or not data.get("account"):
        raise SystemExit(f"Некорректный pending-файл: {PENDING}")

    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ручная проверка TikTok CAPTCHA через xRDP."
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["pending"],
        help="Используй 'pending', чтобы открыть последнюю пойманную CAPTCHA.",
    )
    parser.add_argument("--account")
    parser.add_argument("--url")
    args = parser.parse_args()

    pending: dict = {}

    if args.mode == "pending":
        pending = load_pending()
        account_value = str(pending["account"])
        url = str(pending.get("url") or "https://www.tiktok.com/")
    else:
        if not args.account:
            parser.error("Нужен --account или команда: tiktoker-verify pending")
        account_value = args.account
        url = args.url or "https://www.tiktok.com/"

    display, rdp_user = detect_rdp_display()

    print(f"RDP DISPLAY найден: {display}")
    if rdp_user:
        print(f"RDP пользователь: {rdp_user}")

    account = resolve_account(account_value)
    cookie_path = Path(account["cookie_path"])
    cookies = parse_netscape(cookie_path)

    if not cookies:
        raise SystemExit(f"Cookie-файл пуст: {cookie_path}")

    username = str(account.get("username") or account_value)
    print(f"Аккаунт: @{username}")
    print(f"Открываю: {url}")
    print("Сейчас Chromium появится на рабочем столе aRDP.")

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
                    url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except PlaywrightTimeoutError:
                pass

            page.wait_for_timeout(2000)

            print()
            print("=== ДАЛЬШЕ ТОЛЬКО РУКАМИ ===")
            print("1. Переключись в aRDP — Chromium уже должен быть там.")
            print("2. Если пазл уже виден — реши его.")
            print("3. Если пазла ещё нет — повтори в браузере действие, которое его вызвало.")
            print("4. Когда CAPTCHA полностью исчезнет, вернись в Termius.")
            print("5. Нажми Enter здесь.")
            print()

            while True:
                input("Enter после решения CAPTCHA: ")
                page.wait_for_timeout(1000)

                if captcha_detected(page):
                    print("CAPTCHA всё ещё видна. Реши её до конца.")
                    continue

                print("CAPTCHA больше не обнаружена.")
                break

            backup = save_updated_cookies(context, cookie_path)
            print(f"Cookies обновлены: {cookie_path}")
            print(f"Backup старых cookies: {backup}")

            if args.mode == "pending":
                try:
                    PENDING.unlink()
                except FileNotFoundError:
                    pass

                text = str(pending.get("text") or "")
                if text:
                    retry = (
                        "tiktoker-comment post "
                        f"--account {shlex.quote(account_value)} "
                        f"--url {shlex.quote(url)} "
                        f"--text {shlex.quote(text)}"
                    )
                    print()
                    print("Теперь RDP можно закрыть.")
                    print("Повторить комментарий из Termius:")
                    print(retry)

            print("Ручная проверка завершена.")

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
