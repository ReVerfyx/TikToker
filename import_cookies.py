#!/usr/bin/env python3
import json
import sys
import zipfile
from pathlib import Path


AUTH_NAMES = {"sessionid", "sessionid_ss", "sid_tt"}


def cookie_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("cookies", "Cookies", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("Не найден массив cookies в JSON")


def expiry(c):
    for key in ("expirationDate", "expires", "expiry", "expiration"):
        value = c.get(key)
        if value:
            try:
                return int(float(value))
            except Exception:
                pass
    return 2147483647


def to_netscape(cookies):
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        name = str(c.get("name", "")).strip()
        value = str(c.get("value", ""))
        domain = str(c.get("domain") or c.get("host") or ".tiktok.com")
        path = str(c.get("path") or "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        if not name:
            continue
        lines.append(
            "\t".join([domain, include_sub, path, secure, str(expiry(c)), name, value])
        )
    return "\n".join(lines) + "\n"


def auth_status(cookies):
    names = {str(c.get("name", "")).strip() for c in cookies if isinstance(c, dict)}
    return sorted(names & AUTH_NAMES)


def save_one(cookies, dest, source_name):
    dest.write_text(to_netscape(cookies), encoding="utf-8")
    found = auth_status(cookies)
    if found:
        print(f"[OK] {source_name} -> {dest} | auth: {', '.join(found)}")
    else:
        print(
            f"[WARN] {source_name} -> {dest} | нет sessionid/sessionid_ss/sid_tt; "
            "файл импортирован, но TikTok-авторизация может не пройти"
        )


def main():
    if len(sys.argv) != 2:
        print("Использование: python import_cookies.py cookies.zip")
        raise SystemExit(2)

    src = Path(sys.argv[1])
    out = Path("cookies")
    out.mkdir(exist_ok=True)

    # Не смешиваем новый импорт со старыми account*.txt.
    for old in out.glob("account*.txt"):
        old.unlink()

    imported = 0
    warnings = 0

    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            names = sorted(n for n in z.namelist() if n.lower().endswith(".json"))
            if not names:
                raise SystemExit("В ZIP нет JSON-файлов")

            for i, name in enumerate(names, 1):
                data = json.loads(z.read(name).decode("utf-8-sig"))
                cookies = cookie_list(data)
                dest = out / f"account{i:03d}.txt"
                save_one(cookies, dest, name)
                imported += 1
                if not auth_status(cookies):
                    warnings += 1
    else:
        data = json.loads(src.read_text(encoding="utf-8-sig"))
        cookies = cookie_list(data)
        dest = out / "account001.txt"
        save_one(cookies, dest, src.name)
        imported = 1
        warnings = 0 if auth_status(cookies) else 1

    print()
    print(f"Импортировано аккаунтов: {imported}")
    print(f"Без основной auth-cookie: {warnings}")
    print("TikToker автоматически найдёт все cookies/account*.txt.")


if __name__ == "__main__":
    main()
