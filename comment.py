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

COMMENT_OPEN_SELECTORS = [
    '[data-e2e="comments"][role="tab"]',
    '[data-e2e="comments"]',
    '[data-e2e="comment-icon"][role="button"]',
    '[data-e2e="comment-icon"]',
    'button:has([data-e2e="comment-icon"])',
    'button:has([data-e2e="browse-comment-icon"])',
    '[data-e2e="browse-comment"]',
    'button[aria-label*="comment" i]',
    'button[aria-label*="коммент" i]',
]

COMMENT_BOX_SELECTORS = [
    '[data-e2e="comment-input"] [contenteditable="true"]',
    'div[data-e2e="comment-input"][contenteditable="true"]',
    '[data-e2e="comment-input"][contenteditable="true"]',
    'div[contenteditable="true"][role="textbox"]',
    '[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
]

POST_BUTTON_SELECTORS = [
    '[data-e2e="comment-post"][aria-disabled="false"]',
    'button[data-e2e="comment-post"]',
    'div[data-e2e="comment-post"]',
    '[data-e2e="comment-post"]',
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


def comment_dom_inventory(page) -> None:
    try:
        items = page.locator('[data-e2e*="comment" i]').evaluate_all(
            """els => els.slice(0, 40).map(el => ({
                tag: el.tagName,
                e2e: el.getAttribute('data-e2e') || '',
                role: el.getAttribute('role') || '',
                aria: el.getAttribute('aria-label') || '',
                text: (el.innerText || '').trim().slice(0, 80)
            }))"""
        )
    except Exception:
        items = []

    if not items:
        print("  Comment DOM inventory: ничего не найдено.", flush=True)
        return

    print("  Comment DOM inventory:", flush=True)
    seen = set()
    for item in items:
        key = (item.get("tag"), item.get("e2e"), item.get("role"), item.get("aria"), item.get("text"))
        if key in seen:
            continue
        seen.add(key)
        print(
            "    "
            f"<{item.get('tag','')}> "
            f"data-e2e={item.get('e2e','')!r} "
            f"role={item.get('role','')!r} "
            f"aria={item.get('aria','')!r} "
            f"text={item.get('text','')!r}",
            flush=True,
        )


def auth_diagnostics(page) -> None:
    checks = {
        "login_button": [
            '[data-e2e="top-login-button"]',
            'button:has-text("Log in")',
            'button:has-text("Войти")',
        ],
        "logged_ui": [
            '[data-e2e="profile-icon"]',
            '[data-e2e="inbox-icon"]',
            '[data-e2e="nav-profile"]',
            'a[href*="/messages"]',
        ],
    }

    values = {}
    for name, selectors in checks.items():
        found = 0
        for selector in selectors:
            try:
                found += page.locator(selector).count()
            except Exception:
                continue
        values[name] = found

    print(
        "  Авторизация UI: "
        f"login_button={values['login_button']} | "
        f"logged_ui={values['logged_ui']}",
        flush=True,
    )


def print_comment_network(events: list[tuple[int, str]]) -> None:
    if not events:
        print("  Сеть комментариев: подходящих запросов не замечено.", flush=True)
        return

    print("  Сеть комментариев:", flush=True)
    seen = set()
    for status, path in events[-12:]:
        key = (status, path)
        if key in seen:
            continue
        seen.add(key)
        print(f"    HTTP {status} {path}", flush=True)


def page_diagnostics(page) -> None:
    try:
        body = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        body = ""

    flags = []
    checks = [
        ("login", ("log in", "sign in", "войти")),
        ("captcha", ("captcha", "verify to continue", "подтвердите")),
        ("comments_off", ("comments are turned off", "комментарии отключены")),
        ("comments", ("comments", "комментарии")),
    ]

    for name, needles in checks:
        if any(x in body for x in needles):
            flags.append(name)

    try:
        editable_count = page.locator('[contenteditable="true"]').count()
    except Exception:
        editable_count = -1

    try:
        textarea_count = page.locator("textarea").count()
    except Exception:
        textarea_count = -1

    print(
        "  Диагностика DOM: "
        f"flags={','.join(flags) if flags else 'none'} | "
        f"contenteditable={editable_count} | textarea={textarea_count}",
        flush=True,
    )


def dismiss_overlays(page) -> None:
    selectors = [
        'tiktok-cookie-banner button:has-text("Accept all")',
        'tiktok-cookie-banner button:has-text("Accept")',
        'tiktok-cookie-banner button:has-text("Принять все")',
        'tiktok-cookie-banner button:has-text("Принять")',
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        'button:has-text("Принять все")',
        'button:has-text("Принять")',
        '[data-e2e="modal-close-inner-button"]',
        'button[aria-label="Close"]',
        'button[aria-label="Закрыть"]',
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible(timeout=700):
                print(f"  Закрываю overlay: {selector}", flush=True)
                loc.click(timeout=2500, force=True)
                page.wait_for_timeout(350)
        except Exception:
            continue

    # TikTok иногда оставляет custom-element cookie banner поверх страницы
    # даже когда обычные кнопки не находятся. Не удаляем данные/куки:
    # только запрещаем баннеру перехватывать указатель.
    try:
        banner = page.locator("tiktok-cookie-banner").first
        if banner.count():
            print("  Отключаю перехват кликов cookie-баннером.", flush=True)
            banner.evaluate(
                """el => {
                    el.style.pointerEvents = 'none';
                    el.style.zIndex = '-1';
                    el.setAttribute('aria-hidden', 'true');
                }"""
            )
            page.wait_for_timeout(200)
    except Exception:
        pass


def try_open_comments(page) -> None:
    print("  Проверяю, нужно ли открыть панель комментариев...", flush=True)

    for selector in COMMENT_OPEN_SELECTORS:
        try:
            locators = page.locator(selector)
            count = locators.count()
            if count < 1:
                continue

            for i in range(min(count, 6)):
                locator = locators.nth(i)
                try:
                    if not locator.is_visible(timeout=1200):
                        continue

                    print(
                        f"  Открываю комментарии: {selector} (элемент {i + 1}/{count})",
                        flush=True,
                    )
                    try:
                        locator.scroll_into_view_if_needed(timeout=2500)
                    except Exception:
                        pass

                    locator.click(timeout=5000)
                    page.wait_for_timeout(1800)

                    if (
                        page.locator('[contenteditable="true"]').count() > 0
                        or page.locator("textarea").count() > 0
                        or page.locator('[data-e2e="comment-input"]').count() > 0
                    ):
                        print("  Панель комментариев открыта.", flush=True)
                        return

                    # Если это вкладка Comments, её клик всё равно мог смонтировать sidebar.
                    # Дадим React ещё немного времени перед следующим кандидатом.
                    page.wait_for_timeout(700)
                except Exception:
                    continue
        except Exception:
            continue

    print(
        "  Кнопка комментариев найдена/проверена, но поле ввода пока не появилось.",
        flush=True,
    )


def first_visible(page, selectors: list[str], timeout_ms: int = 7000):
    last_error = None

    for selector in selectors:
        print(f"  Ищу элемент: {selector}", flush=True)
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            print(f"  Найден: {selector}", flush=True)
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
        print("[1/7] Запускаю Playwright...", flush=True)
        with sync_playwright() as pw:
            print("[2/7] Запускаю Chromium...", flush=True)
            browser = pw.chromium.launch(
                headless=True,
                timeout=20000,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            print("[3/7] Создаю сессию аккаунта...", flush=True)
            context = browser.new_context(
                locale="ru-RU",
                viewport={"width": 1920, "height": 1080},
                screen={"width": 1920, "height": 1080},
                device_scale_factor=1,
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0.0.0 Safari/537.36"
                ),
            )
            context.set_default_timeout(10000)
            context.set_default_navigation_timeout(25000)

            try:
                context.add_cookies(cookies)
                page = context.new_page()

                comment_network = []

                def _capture_response(response):
                    try:
                        from urllib.parse import urlsplit
                        u = urlsplit(response.url)
                        path = u.path.lower()
                        if "comment" in path or "/api/" in path and "comment" in response.url.lower():
                            comment_network.append((response.status, f"{u.scheme}://{u.netloc}{u.path}"))
                    except Exception:
                        pass

                page.on("response", _capture_response)

                print("[4/7] Открываю видео...", flush=True)
                try:
                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=25000,
                    )
                except PlaywrightTimeoutError:
                    print("  Страница грузилась слишком долго, продолжаю с уже загруженным DOM.", flush=True)

                page.wait_for_timeout(1800)

                # Короткая vt.tiktok.com ссылка редиректит с tracking query.
                # Повторное открытие canonical video URL часто монтирует полный desktop layout.
                try:
                    from urllib.parse import urlsplit, urlunsplit
                    parts = urlsplit(page.url)
                    if "tiktok.com" in parts.netloc.lower() and "/video/" in parts.path and parts.query:
                        canonical_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
                        print(f"  Канонический URL: {canonical_url}", flush=True)
                        page.goto(canonical_url, wait_until="commit", timeout=12000)
                        page.wait_for_timeout(2500)
                except PlaywrightTimeoutError:
                    print("  Каноническая страница грузилась долго, продолжаю.", flush=True)
                except Exception as exc:
                    print(f"  Не удалось переоткрыть canonical URL: {exc}", flush=True)

                print(f"  Итоговый URL: {page.url}", flush=True)
                dismiss_overlays(page)
                page_diagnostics(page)
                auth_diagnostics(page)
                print_comment_network(comment_network)
                comment_dom_inventory(page)

                if "/login" in page.url.lower():
                    raise RuntimeError(
                        "TikTok отправил на страницу входа: cookies недействительны."
                    )

                print("[5/7] Открываю комментарии и ищу поле...", flush=True)
                try:
                    page.mouse.wheel(0, 900)
                    page.wait_for_timeout(1200)
                except Exception:
                    pass

                dismiss_overlays(page)
                try_open_comments(page)
                page_diagnostics(page)
                auth_diagnostics(page)
                print_comment_network(comment_network)
                comment_dom_inventory(page)

                try:
                    box = first_visible(page, COMMENT_BOX_SELECTORS, timeout_ms=3000)
                except Exception:
                    # Иногда TikTok использует обычный textarea вместо contenteditable.
                    print("  Пробую textarea...", flush=True)
                    box = first_visible(
                        page,
                        [
                            'textarea[placeholder*="comment" i]',
                            'textarea[placeholder*="коммент" i]',
                            "textarea",
                        ],
                        timeout_ms=3000,
                    )

                print("[6/7] Ввожу текст...", flush=True)
                box.click(force=True, timeout=7000)
                page.wait_for_timeout(250)

                # TikTok использует React contenteditable. fill() иногда меняет DOM,
                # но не генерирует нужные input-события, из-за чего кнопка остаётся disabled.
                try:
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                except Exception:
                    pass

                page.keyboard.insert_text(text)
                page.wait_for_timeout(900)

                try:
                    current_text = (box.inner_text(timeout=2000) or "").strip()
                except Exception:
                    current_text = ""

                print(f"  Текст в поле: {current_text!r}", flush=True)

                if text not in current_text:
                    print(
                        "  Первый ввод не зарегистрировался, пробую через JS events.",
                        flush=True,
                    )
                    box.evaluate(
                        """(el, value) => {
                            el.focus();
                            el.textContent = value;
                            el.dispatchEvent(new InputEvent('input', {
                                bubbles: true,
                                inputType: 'insertText',
                                data: value
                            }));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        }""",
                        text,
                    )
                    page.wait_for_timeout(900)

                print("[7/7] Ищу кнопку отправки...", flush=True)
                post = first_visible(page, POST_BUTTON_SELECTORS, timeout_ms=7000)

                aria_disabled = post.get_attribute("aria-disabled")
                native_disabled = post.is_disabled()

                if aria_disabled == "true" or native_disabled:
                    try:
                        field_text = (box.inner_text(timeout=2000) or "").strip()
                    except Exception:
                        field_text = ""

                    raise RuntimeError(
                        "Кнопка публикации осталась disabled после ввода. "
                        f"Текст в поле={field_text!r}. "
                        "TikTok не принял событие ввода."
                    )

                dismiss_overlays(page)

                try:
                    post.click(timeout=5000)
                except PlaywrightTimeoutError:
                    print(
                        "  Обычный клик заблокирован overlay — пробую force click.",
                        flush=True,
                    )
                    dismiss_overlays(page)
                    post.click(timeout=5000, force=True)

                page.wait_for_timeout(2000)

                if "/login" in page.url.lower():
                    raise RuntimeError(
                        "После отправки TikTok запросил повторный вход."
                    )

                print("Комментарий отправлен.", flush=True)
                write_history(account, url, text, "sent")

            finally:
                print("Закрываю сессию браузера.", flush=True)
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
