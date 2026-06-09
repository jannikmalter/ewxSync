#!/usr/bin/env python3
"""Fetch the EventWorx locale JSON and extract Common.JobStatusMap.

EwLocales loads its translation table at runtime from
``<base>/.../resources/locales/<locale>.json`` (see app.js EwLocales.updateLocale).
This grabs that file and dumps the per-docType JobStatusMap label tree — the
authoritative source for status display labels.

Tries an anonymous GET first (the runtime request carries no auth token, so the
file is probably static). Falls back to a READONLY login like ewxApiTest.py only
if the anonymous fetch is rejected.
"""
import json, logging, os, sys, time, requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

EVENTWORX_BASE = os.getenv("EVENTWORX_BASE")
USERNAME = os.getenv("EVENTWORX_USERNAME")
PASSWORD = os.getenv("EVENTWORX_PASSWORD")

COMMON_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0",
    "Origin": EVENTWORX_BASE,
    "Referer": f"{EVENTWORX_BASE}/eventworx/",
}

LOCALES = ["de", "en"]
PATH_TEMPLATES = [
    "/eventworx/resources/locales/{loc}.json",
    "/resources/locales/{loc}.json",
]

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")


def try_login(username: str, password: str):
    logging.info("Logging in (READONLY)…")
    s = requests.Session()
    payload = {"license": "READONLY", "forceLogoff": "false",
               "username": username, "password": password}
    headers = {**COMMON_HEADERS,
               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    r = s.post(f"{EVENTWORX_BASE}/backend/login", data=payload, headers=headers)
    if "LICENSE-NOT-AVAILABLE" in r.text:
        logging.error("READONLY license already in use (daemon running?).")
        return None, None
    token = r.headers.get("x-auth-token")
    if not token:
        logging.error("No auth token received.")
        return None, None
    logging.info("Logged in.")
    return s, token


def logout(session, token):
    headers = {**COMMON_HEADERS, "X-AUTH-TOKEN": token,
               "Content-Type": "application/json; charset=UTF-8"}
    try:
        session.post(f"{EVENTWORX_BASE}/backend/logout", headers=headers)
    finally:
        logging.info("Logged out.")


def fetch_locale(session, loc, token=None):
    headers = dict(COMMON_HEADERS)
    if token:
        headers["X-AUTH-TOKEN"] = token
    for tmpl in PATH_TEMPLATES:
        url = f"{EVENTWORX_BASE}{tmpl.format(loc=loc)}"
        r = session.get(url, headers=headers)
        logging.info("GET %s -> HTTP %s (%d bytes)", url, r.status_code, len(r.content))
        if r.status_code == 200:
            try:
                return url, r.json()
            except ValueError:
                logging.warning("  200 but body is not JSON; skipping.")
    return None, None


def dump(loc, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = os.path.join(CACHE_DIR, f"locale_{loc}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logging.info("Wrote %s (%d top-level keys)", out, len(data))
    jsm = (data.get("Common") or {}).get("JobStatusMap")
    if jsm:
        print(f"\n=== Common.JobStatusMap ({loc}) ===")
        print(json.dumps(jsm, indent=2, ensure_ascii=False))
    else:
        print(f"[{loc}] No Common.JobStatusMap key found. "
              f"Top-level keys: {sorted(data.keys())[:20]}")


def main():
    if not EVENTWORX_BASE:
        sys.exit("EVENTWORX_BASE must be set in .env")

    anon = requests.Session()
    session, token = None, None
    try:
        for loc in LOCALES:
            url, data = fetch_locale(anon, loc)               # anonymous first
            if not data and token is None and USERNAME and PASSWORD:
                session, token = try_login(USERNAME, PASSWORD)  # one-time fallback
            if not data and token:
                url, data = fetch_locale(session, loc, token)
            if data:
                dump(loc, data)
            else:
                logging.error("Could not fetch locale '%s' from any candidate path.", loc)
    finally:
        if session and token:
            logout(session, token)


if __name__ == "__main__":
    main()
