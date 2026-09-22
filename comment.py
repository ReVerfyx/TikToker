#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from accounts import parse_netscape

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / "data" / "accounts.json"
HISTORY = ROOT / "data" / "comments.csv"

COMMENT_BOX_SELECTORS = [
    '[data-e2e="comment-input"] [contenteditable="true"]',
    'div[data-e2e="comment-input"][contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
]

POST_BUTTON_SELECTORS = [
    '[data-e2e="comment-post"][aria-disabled="false"]',
    'button[data-e2e="comment-post"]',
]


def load_registry() -> list[dict]:
    if not REGISTRY.is_file():
        raise SystemExit(
            "Нет data/accounts.json. Сначала запусти: tiktoker-accounts scan"
        )

    data = json.loads(REGISTRY.read_text("utf-8"))
    if not isinstance(data, list):
        raise SystemExit("Некорректный data/accounts.json")
    return data


def resolve_account(value: str) -> dict:
    raw_value = value.strip()
    needle = raw_value.lstrip("@").lower()
    rows = load_registry()

    active_rows = [
        row for row in rows
        if row.get("status") == "public" and row.get("enabled") is True
    ]

    # Удобный выбор по номеру из списка активных аккаунтов:
    # --account 1 = первый, --account 2 = второй и т.д.
    if raw_value.isdigit():
        index = int(raw_value)
        if index < 1 or index > len(active_rows):
            raise SystemExit(
                f"Номер аккаунта вне диапазона: {index}. "
                f"Доступно активных аккаунтов: {len(active_rows)}"
            )

        row = dict(active_rows[index - 1])
        cookie_path = ROOT / "cookies" / str(row.get("cookie_file") or "")
        if not cookie_path.is_file():
            raise SystemExit(f"Нет cookie-файла: {cookie_path}")

        row["cookie_path"] = cookie_path
        return row

    matches = []

    for row in rows:
        username = str(row.get("username") or "").lstrip("@").lower()
        cookie_file = str(row.get("cookie_file") or "")
        cookie_stem = Path(cookie_file).stem.lower()

        if needle in {username, cookie_file.lower(), cookie_stem}:
            matches.append(row)

    if not matches:
        raise SystemExit(f"Аккаунт не найден в реестре: {value}")

    if len(matches) > 1:
        raise SystemExit(f"Найдено несколько совпадений для: {value}")

    row = matches[0]

    if row.get("status") != "public" or row.get("enabled") is not True:
        raise SystemExit(
            f"Аккаунт {value} не активен: status={row.get('status')}"
        )

    cookie_path = ROOT / "cookies" / str(row.get("cookie_file") or "")
    if not cookie_path.is_file():
        raise SystemExit(f"Нет cookie-файла: {cookie_path}")

    row = dict(row)
    row["cookie_path"] = cookie_path
    return row


def first_visible(page, selectors: list[str], timeout_ms: int = 15000):
    last_error = None

    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "Не найден элемент TikTok. Возможно, интерфейс изменился "
        f"или комментарии отключены. Последняя ошибка: {last_error}"
    )


def write_history(
    account: dict,
    url: str,
    text: str,
    status: str,
    error: str = "",
) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    exists = HISTORY.exists()

    with HISTORY.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)

        if not exists:
            writer.writerow(
                [
                    "timestamp",
                    "username",
                    "cookie_file",
                    "url",
                    "text",
                    "status",
                    "error",
                ]
            )

        writer.writerow(
            [
                datetime.now(timezone.utc).isoformat(),
                account.get("username") or "",
                account.get("cookie_file") or "",
                url,
                text,
                status,
                error,
            ]
        )


def resolve_accounts_list(value: str) -> list[dict]:
    parts = [x.strip() for x in value.split(",") if x.strip()]
    if not parts:
        raise SystemExit("Не указаны аккаунты.")

    result = []
    seen = set()

    for part in parts:
        row = resolve_account(part)
        key = str(row.get("cookie_file") or row.get("username") or part)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)

    return result


def preview_commands(url: str, text: str, accounts_value: str) -> None:
    if "tiktok.com/" not in url.lower():
        raise SystemExit("Нужна ссылка на TikTok-видео.")

    text = text.strip()
    if not text:
        raise SystemExit("Комментарий пустой.")

    accounts = resolve_accounts_list(accounts_value)

    print(f"Выбрано аккаунтов: {len(accounts)}")
    print()

    for i, row in enumerate(accounts, start=1):
        username = str(row.get("username") or "").strip()
        account_arg = f"@{username}" if username else str(row.get("cookie_file") or "")
        safe_url = shlex.quote(url)
        safe_text = shlex.quote(text)
        safe_account = shlex.quote(account_arg)

        print(f"{i}. {account_arg}")
        print(
            "tiktoker-comment post "
            f"--url {safe_url} "
            f"--text {safe_text} "
            f"--account {safe_account}"
        )
        print()


def next_comments(url: str, text: str, accounts_value: str) -> None:
    accounts = resolve_accounts_list(accounts_value)

    if "tiktok.com/" not in url.lower():
        raise SystemExit("Нужна ссылка на TikTok-видео.")

    text = text.strip()
    if not text:
        raise SystemExit("Комментарий пустой.")

    print(f"Выбрано аккаунтов: {len(accounts)}")
    print("Перед каждым комментарием потребуется подтверждение.")
    print("Команды: y = отправить, s = пропустить, q = выйти")
    print()

    for index, row in enumerate(accounts, start=1):
        username = str(row.get("username") or "").strip()
        account_arg = f"@{username}" if username else str(row.get("cookie_file") or "")

        print(f"[{index}/{len(accounts)}] Аккаунт: {account_arg}")
        print(f"Текст: {text}")

        while True:
            answer = input("Отправить? [y/s/q]: ").strip().lower()

            if answer in {"q", "quit", "exit"}:
                print("Остановлено.")
                return

            if answer in {"s", "skip"}:
                print("Пропущено.")
                print()
                break

            if answer in {"y", "yes", "да", "д"}:
                try:
                    post_comment(url, text, account_arg)
                except Exception as exc:
                    print(f"Ошибка для {account_arg}: {exc}", file=sys.stderr)
                print()
                break

            print("Введите y, s или q.")


def post_comment(url: str, text: str, account_name: str) -> None:
    if "tiktok.com/" not in url.lower():
        raise SystemExit("Нужна ссылка на TikTok-видео.")

    text = text.strip()
    if not text:
        raise SystemExit("Комментарий пустой.")

    account = resolve_account(account_name)
    cookies = parse_netscape(account["cookie_path"])

    if not cookies:
        raise SystemExit("Cookie-файл пуст.")

    username = str(account.get("username") or account_name)

    print(f"Аккаунт: @{username}")
    print(f"Видео: {url}")
    print(f"Комментарий: {text}")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

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
                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                except PlaywrightTimeoutError:
                    pass

                page.wait_for_timeout(2000)

                if "/login" in page.url.lower():
                    raise RuntimeError(
                        "TikTok отправил на страницу входа: cookies недействительны."
                    )

                box = first_visible(page, COMMENT_BOX_SELECTORS)

                box.click(force=True)
                page.wait_for_timeout(300)

                try:
                    box.fill(text)
                except Exception:
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    page.keyboard.insert_text(text)

                page.wait_for_timeout(600)

                post = first_visible(page, POST_BUTTON_SELECTORS, timeout_ms=10000)

                disabled = post.get_attribute("aria-disabled")
                if disabled == "true":
                    raise RuntimeError(
                        "Кнопка публикации недоступна. "
                        "Возможно, комментарии отключены."
                    )

                post.click()
                page.wait_for_timeout(2500)

                if "/login" in page.url.lower():
                    raise RuntimeError(
                        "После отправки TikTok запросил повторный вход."
                    )

                print("Комментарий отправлен.")
                write_history(account, url, text, "sent")

            finally:
                context.close()
                browser.close()

    except Exception as exc:
        write_history(account, url, text, "error", str(exc)[:1000])
        raise


def show_history(limit: int) -> None:
    if not HISTORY.is_file():
        print("История комментариев пока пустая.")
        return

    rows = list(csv.DictReader(HISTORY.open("r", encoding="utf-8-sig")))
    rows = rows[-max(1, limit):]

    for row in rows:
        print(
            f"{row['timestamp']} | @{row['username']} | "
            f"{row['status']} | {row['url']} | {row['text']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Один TikTok-комментарий с явно выбранного аккаунта."
    )
    sub = parser.add_subparsers(dest="command")

    post = sub.add_parser("post")
    post.add_argument("--url", required=True)
    post.add_argument("--text", required=True)
    post.add_argument("--account", required=True)

    preview = sub.add_parser("preview")
    preview.add_argument("--url", required=True)
    preview.add_argument("--text", required=True)
    preview.add_argument(
        "--accounts",
        required=True,
        help="Номера или имена через запятую, например: 1,2,3 или @user1,@user2",
    )

    nxt = sub.add_parser("next")
    nxt.add_argument("--url", required=True)
    nxt.add_argument("--text", required=True)
    nxt.add_argument(
        "--accounts",
        required=True,
        help="Номера или имена через запятую, например: 1,2,3,4",
    )

    history = sub.add_parser("history")
    history.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()

    if args.command == "post":
        post_comment(args.url, args.text, args.account)
        return

    if args.command == "preview":
        preview_commands(args.url, args.text, args.accounts)
        return

    if args.command == "next":
        next_comments(args.url, args.text, args.accounts)
        return

    if args.command == "history":
        show_history(args.limit)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
