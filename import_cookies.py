#!/usr/bin/env python3
import json
import re
import sys
import urllib.parse
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


def json_to_netscape(cookies):
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


def parse_netscape(text):
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            rows.append(parts[:7])
    return rows


def ensure_auth_cookie(text):
    rows = parse_netscape(text)
    names = {r[5] for r in rows}
    if names & AUTH_NAMES:
        return text, False

    sid_guard = next((r[6] for r in rows if r[5] == "sid_guard"), "")
    if not sid_guard:
        return text, False

    decoded = urllib.parse.unquote(sid_guard)
    candidate = decoded.split("|", 1)[0].strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", candidate):
        return text, False

    # TikTok sid_guard commonly starts with the same 32-byte session token
    # used by sessionid/sid_tt. This is a compatibility fallback for exports
    # where HttpOnly sessionid was omitted.
    extra = [
        [".tiktok.com", "TRUE", "/", "TRUE", "2147483647", "sessionid", candidate],
        [".tiktok.com", "TRUE", "/", "TRUE", "2147483647", "sessionid_ss", candidate],
        [".tiktok.com", "TRUE", "/", "FALSE", "2147483647", "sid_tt", candidate],
    ]

    base = text.rstrip("\n")
    if not base.startswith("# Netscape HTTP Cookie File"):
        base = "# Netscape HTTP Cookie File\n" + base
    base += "\n" + "\n".join("\t".join(r) for r in extra) + "\n"
    return base, True


def auth_status(text):
    names = {r[5] for r in parse_netscape(text)}
    return sorted(names & AUTH_NAMES)


def save_text(text, dest, source_name):
    fixed, derived = ensure_auth_cookie(text)
    dest.write_text(fixed, encoding="utf-8")
    found = auth_status(fixed)
    suffix = " | sessionid восстановлен из sid_guard" if derived else ""
    if found:
        print(f"[OK] {source_name} -> {dest} | auth: {', '.join(found)}{suffix}")
    else:
        print(
            f"[WARN] {source_name} -> {dest} | нет sessionid/sessionid_ss/sid_tt "
            "и не удалось восстановить их из sid_guard"
        )
    return bool(found), derived


def import_json_bytes(raw):
    data = json.loads(raw.decode("utf-8-sig"))
    return json_to_netscape(cookie_list(data))


def main():
    if len(sys.argv) != 2:
        print("Использование: python import_cookies.py cookies.zip")
        raise SystemExit(2)

    src = Path(sys.argv[1])
    out = Path("cookies")
    out.mkdir(exist_ok=True)

    for old in out.glob("account*.txt"):
        old.unlink()

    imported = 0
    valid = 0
    derived = 0

    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            names = sorted(
                n for n in z.namelist()
                if n.lower().endswith((".json", ".txt"))
            )
            if not names:
                raise SystemExit("В ZIP нет JSON/TXT cookies-файлов")

            for i, name in enumerate(names, 1):
                raw = z.read(name)
                if name.lower().endswith(".json"):
                    text = import_json_bytes(raw)
                else:
                    text = raw.decode("utf-8-sig", errors="replace")

                source_stem = Path(name).stem
                if source_stem.isdigit():
                    dest = out / f"account_{source_stem}.txt"
                else:
                    dest = out / f"account{i:03d}.txt"
                ok, was_derived = save_text(text, dest, name)
                imported += 1
                valid += int(ok)
                derived += int(was_derived)
    else:
        raw = src.read_bytes()
        if src.suffix.lower() == ".json":
            text = import_json_bytes(raw)
        else:
            text = raw.decode("utf-8-sig", errors="replace")
        dest = out / "account001.txt"
        ok, was_derived = save_text(text, dest, src.name)
        imported = 1
        valid = int(ok)
        derived = int(was_derived)

    print()
    print(f"Импортировано аккаунтов: {imported}")
    print(f"С auth-cookie после обработки: {valid}")
    print(f"sessionid восстановлен из sid_guard: {derived}")
    print("TikToker автоматически найдёт все cookies/account*.txt.")


if __name__ == "__main__":
    main()
