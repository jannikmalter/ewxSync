#!/usr/bin/env python3
"""Quick-and-dirty Eventworx API tester.

Logs in, fires one configurable request, dumps the response into a JSON file,
then logs out. Edit REQUEST below to change endpoint / method / params / body.
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

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
OUTPUT_FILE = os.path.join(CACHE_DIR, "ewx_api_test_response.json")

# -------- REQUEST TO TEST --------
# Tweak this dict to point at any backend endpoint.
# `path` is appended to EVENTWORX_BASE. `_dc` (cache-buster) is added automatically.
#
# Filter syntax (observed from the website's own requests):
#   {"property": "<field>|<case>", "operator": "<op>", "value": <value>}
# `<case>` is either `*` (applies to every row) or a case name like `case_offer`
# / `case_order`. Multiple cases are OR-ed; filters within one case are AND-ed.
# Known Solr fields: subType, status, archived, activeFrom, endDate,
#   extraDate3 (creation), lastModification (modification time — note: the JSON
#   response field is called `modificationDate`, but Solr indexes it as
#   `lastModification`).
# Known operators: in, <, >, <=, >=, =.

now_ms = int(time.time() * 1000)
day_ago_ms = now_ms - 10 * 60 * 1000

SORT_FIELD = "lastModification"

REQUEST = {
    "method": "GET",
    "path": "/backend/job",
    "params": {
        "opts": json.dumps({"calculateStockConflicts": False}),
        "page": 1,
        "start": 0,
        "limit": 50,
        "sort": json.dumps([{"property": SORT_FIELD, "direction": "DESC"}]),
        "filter": json.dumps([
            {"property": f"{SORT_FIELD}|*", "operator": ">", "value": day_ago_ms},
        ]),
    },
    "json": None,   # set a dict here for JSON body (POST/PUT)
    "data": None,   # set a dict here for form-encoded body
}
# ---------------------------------


def try_login(username: str, password: str):
    logging.info("Logging in…")
    s = requests.Session()
    payload = {"license": "READONLY", "forceLogoff": "false",
               "username": username, "password": password}
    headers = {**COMMON_HEADERS,
               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    r = s.post(f"{EVENTWORX_BASE}/backend/login", data=payload, headers=headers)
    if "LICENSE-NOT-AVAILABLE" in r.text:
        logging.error("License already in use.")
        sys.exit(1)
    token = r.headers.get("x-auth-token")
    if not token:
        logging.error("No auth token received.")
        sys.exit(1)
    logging.info("Logged in.")
    return s, token


def logout(session: requests.Session, token: str):
    headers = {**COMMON_HEADERS,
               "X-AUTH-TOKEN": token,
               "Content-Type": "application/json; charset=UTF-8"}
    try:
        session.post(f"{EVENTWORX_BASE}/backend/logout", headers=headers)
    finally:
        logging.info("Logged out.")


def run_request(session: requests.Session, token: str, spec: dict) -> dict:
    method = spec.get("method", "GET").upper()
    url = f"{EVENTWORX_BASE}{spec['path']}"
    params = dict(spec.get("params") or {})
    params.setdefault("_dc", str(int(time.time() * 1000)))

    headers = {**COMMON_HEADERS, "X-AUTH-TOKEN": token}
    if spec.get("json") is not None:
        headers["Content-Type"] = "application/json; charset=UTF-8"

    logging.info("%s %s", method, url)
    r = session.request(method, url, headers=headers, params=params,
                        json=spec.get("json"), data=spec.get("data"))
    logging.info("HTTP %s (%d bytes)", r.status_code, len(r.content))

    try:
        body = r.json()
    except ValueError:
        body = r.text

    return {
        "request": {
            "method": method,
            "url": url,
            "params": params,
            "json": spec.get("json"),
            "data": spec.get("data"),
        },
        "response": {
            "status": r.status_code,
            "headers": dict(r.headers),
            "body": body,
        },
    }


def main():
    if not (EVENTWORX_BASE and USERNAME and PASSWORD):
        logging.error("EVENTWORX_BASE, EVENTWORX_USERNAME, EVENTWORX_PASSWORD must be set in .env")
        sys.exit(1)

    session, token = try_login(USERNAME, PASSWORD)
    try:
        result = run_request(session, token, REQUEST)
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        logging.info("Wrote %s", OUTPUT_FILE)
    finally:
        logout(session, token)


if __name__ == "__main__":
    main()
