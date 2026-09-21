#!/usr/bin/env python3
import json
import sys
import zipfile
from pathlib import Path


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


def main():
    if len(sys.argv) != 2:
        print("Использование: python import_cookies.py cookies.zip")
        raise SystemExit(2)

    src = Path(sys.argv[1])
    out = Path("cookies")
    out.mkdir(exist_ok=True)

    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".json")]
            if not names:
                raise SystemExit("В ZIP нет JSON-файлов")
            for i, name in enumerate(names, 1):
                data = json.loads(z.read(name).decode("utf-8-sig"))
                dest = out / f"account{i}.txt"
                dest.write_text(to_netscape(cookie_list(data)), encoding="utf-8")
                print(f"{name} -> {dest}")
    else:
        data = json.loads(src.read_text(encoding="utf-8-sig"))
        dest = out / "account1.txt"
        dest.write_text(to_netscape(cookie_list(data)), encoding="utf-8")
        print(f"{src} -> {dest}")


if __name__ == "__main__":
    main()
