#!/usr/bin/env python3
from dataclasses import dataclass, asdict
import time, json, logging, requests, sys, os
from datetime import datetime, timezone
from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError

load_dotenv()

force_notion_sync = False


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

# -------- CONFIG --------
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID  = os.getenv("DATABASE_ID")   

EVENTWORX_BASE = os.getenv("EVENTWORX_BASE")
LOCAL_CACHE = "local_eventworx_projects.json"

USERNAME = os.getenv("EVENTWORX_USERNAME")
PASSWORD = os.getenv("EVENTWORX_PASSWORD")

COMMON_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0",
    "Origin": EVENTWORX_BASE,
    "Referer": f"{EVENTWORX_BASE}/eventworx/",
}

# Status sets derived from Eventworx's own UI filter requests for "active orders/offers".
# Offers include "accepted" as an active state; orders do not.
ACTIVE_ORDER_STATUSES = {"draft", "sent", "open"}
ACTIVE_OFFER_STATUSES = {"draft", "sent", "open", "accepted"}
CLOSED_STATUSES       = {"finished", "completed", "fullypaid"}
CANCELLED_STATUSES    = {"rejected", "cancelled"}

# Priority for picking the representative doc on closed projects.
# Invoice is the final stage of the lifecycle and carries the most complete information.
CLOSED_REP_PRIORITY = {"invoice": 4, "deliverynote": 3, "order": 2, "offer": 1}


# --------------- DATA MODELS ---------------
@dataclass
class Document:
    jobNumber: str
    projectNumber: str
    docType: str
    dealType: str           # "rent" | "sale" | None — only rent is tracked as active
    title: str
    status: str
    activation: str | None  # None | "archived" | "active" | "deleted"
    modificationDate: int | None
    overallPriceValue: float | None
    endDate: int | None     # raw ms timestamp — used to check if an offer period has passed
    rentStartDate: str | None
    rentEndDate: str | None
    jobCategoryNames: list[str] = None

@dataclass
class ProjectSummary:
    projectNumber: str
    title: str
    status: str
    currentPrice: float | None
    rentStartDate: str | None
    rentEndDate: str | None
    representativeJob: str
    representativeDocType: str
    categories: list[str] = None
    has_order: bool = False
    has_offer: bool = False
    has_request: bool = False
    has_delivery: bool = False
    has_invoice: bool = False
    icon: str | None = None  # URL or emoji string if a Notion icon is set; None means no icon

    def __post_init__(self):
        if self.categories is None:
            self.categories = []
        if not isinstance(self.categories, list):
            self.categories = []


# --------------- HELPERS ---------------
def normalize_eventworx_datetime(timestamp: str | int | None) -> str | None:
    if not timestamp:
        return None
    try:
        if isinstance(timestamp, str):
            timestamp = int(timestamp)
        dt = datetime.fromtimestamp(timestamp / 1000.0, tz=timezone.utc)
        # Truncate to the minute to avoid jitter-based spurious diffs on re-sync
        return dt.replace(second=0, microsecond=0).isoformat()
    except (ValueError, TypeError):
        return None

def normalize_notion_datetime(dt_str: str | None) -> str | None:
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()
    except (ValueError, TypeError):
        return None

def make_project_summary(projectNumber, title, status, currentPrice, rentStartDate, rentEndDate,
                         representativeJob, representativeDocType, categories=None,
                         has_order=False, has_offer=False, has_request=False,
                         has_delivery=False, has_invoice=False, icon=None):
    if not isinstance(categories, list):
        categories = []
    return ProjectSummary(
        projectNumber=projectNumber or "",
        title=title or "",
        status=status or "",
        currentPrice=float(currentPrice) if currentPrice is not None else None,
        rentStartDate=rentStartDate,
        rentEndDate=rentEndDate,
        representativeJob=representativeJob or "",
        representativeDocType=representativeDocType or "",
        categories=categories,
        has_order=bool(has_order),
        has_offer=bool(has_offer),
        has_request=bool(has_request),
        has_delivery=bool(has_delivery),
        has_invoice=bool(has_invoice),
        icon=icon or None,
    )

def _first_non_null(docs: list[Document], attr: str):
    """Return the first non-null value for attr, preferring the most recently modified doc."""
    for d in sorted(docs, key=lambda d: d.modificationDate or 0, reverse=True):
        v = getattr(d, attr, None)
        if v is not None and v != "":
            return v
    return None


# --------------- PROJECT CLASSIFICATION ---------------
def classify_project(docs: list[Document], now_ms: int) -> tuple[str, Document]:
    """
    Determine a project's status and pick its representative document.

    Mirrors Eventworx's own "active" filter logic (reverse-engineered from UI requests):
    - A live order (non-archived, dealType=rent, status in ACTIVE_ORDER_STATUSES) makes
      the project Aktiv. When a live order exists, offers are implicitly superseded.
    - A live offer (same conditions plus status in ACTIVE_OFFER_STATUSES and endDate still
      in the future) makes the project Aktiv only when no live order exists.
    - Otherwise the project is closed; the terminal status of the most recently modified
      doc determines Abgeschlossen vs. Storniert.

    The representative doc is whichever live doc drives the Aktiv state, or the most
    recently modified doc for closed projects (used for title, price, and dates).
    """
    # Live order takes precedence — an order being placed supersedes any open offer.
    live_orders = [d for d in docs
                   if d.docType == "order"
                   and d.status in ACTIVE_ORDER_STATUSES
                   and d.activation != "archived"
                   and d.dealType == "rent"]
    if live_orders:
        return "Aktiv", max(live_orders, key=lambda d: d.modificationDate or 0)

    # Offers come in numbered variants that share the same jobNumber. Only the latest
    # variant reflects the current state of that offer chain — a rejected variant 2
    # supersedes a still-open variant 1 even when variant 1 is not archived.
    latest_offer_per_job: dict[str, Document] = {}
    for d in docs:
        if d.docType == "offer":
            existing = latest_offer_per_job.get(d.jobNumber)
            if existing is None or (d.modificationDate or 0) > (existing.modificationDate or 0):
                latest_offer_per_job[d.jobNumber] = d

    # Live offer — only relevant when no live order exists.
    # endDate > now mirrors Eventworx excluding offers whose event period has passed.
    live_offers = [d for d in latest_offer_per_job.values()
                   if d.status in ACTIVE_OFFER_STATUSES
                   and d.activation != "archived"
                   and d.dealType == "rent"
                   and (d.endDate is None or d.endDate > now_ms)]
    if live_offers:
        return "Aktiv", max(live_offers, key=lambda d: d.modificationDate or 0)

    # No active document — project is closed.
    # Rep is the highest-priority doc type (invoice > deliverynote > order > offer),
    # with modificationDate as tiebreaker. Status is read from the most recent doc.
    rep = max(docs, key=lambda d: (CLOSED_REP_PRIORITY.get(d.docType, 0), d.modificationDate or 0))
    by_recency = sorted(docs, key=lambda d: d.modificationDate or 0, reverse=True)
    for d in by_recency:
        if d.status in CANCELLED_STATUSES:
            return "Storniert", rep
        if d.status in CLOSED_STATUSES:
            return "Abgeschlossen", rep

    return "Abgeschlossen", rep


def aggregate_projects(all_docs: list[Document]) -> list[ProjectSummary]:
    now_ms = int(time.time() * 1000)

    by_project: dict[str, list[Document]] = {}
    for d in all_docs:
        if d.projectNumber:
            by_project.setdefault(d.projectNumber, []).append(d)

    summaries = []
    for pn, docs in by_project.items():
        status, rep = classify_project(docs, now_ms)
        doc_types = {d.docType for d in docs}

        # Merge categories across all docs — they can be set on any document type,
        # not just the representative one.
        all_cats = sorted({n for d in docs for n in (d.jobCategoryNames or []) if n})

        # Use rep values for price/dates; fall back to most recent non-null across docs
        # to avoid losing data when the representative doc has empty fields.
        price     = rep.overallPriceValue if rep.overallPriceValue is not None else _first_non_null(docs, "overallPriceValue")
        rent_start = rep.rentStartDate or _first_non_null(docs, "rentStartDate")
        rent_end   = rep.rentEndDate   or _first_non_null(docs, "rentEndDate")

        summaries.append(ProjectSummary(
            projectNumber=pn,
            title=rep.title or f"Project {pn}",
            status=status,
            currentPrice=price,
            rentStartDate=rent_start,
            rentEndDate=rent_end,
            representativeJob=rep.jobNumber,
            representativeDocType=rep.docType,
            categories=all_cats,
            has_order="order"       in doc_types,
            has_offer="offer"       in doc_types,
            has_request="request"   in doc_types,
            has_delivery="deliverynote" in doc_types,
            has_invoice="invoice"   in doc_types,
        ))
    return summaries


# --------------- EVENTWORX API ---------------
def try_login(username, password):
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

def logout(session, token):
    headers = {**COMMON_HEADERS,
               "X-AUTH-TOKEN": token,
               "Content-Type": "application/json; charset=UTF-8"}
    try:
        session.post(f"{EVENTWORX_BASE}/backend/logout", headers=headers)
    finally:
        logging.info("Logged out.")

def fetch_job_categories(session, token):
    logging.info("Fetching job categories…")
    headers = {**COMMON_HEADERS, "X-AUTH-TOKEN": token}
    params = {"_dc": str(int(time.time() * 1000)), "withoutIndex": "true"}
    try:
        resp = session.get(f"{EVENTWORX_BASE}/backend/category/tree/job/groups/rent",
                           headers=headers, params=params)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logging.exception("Failed to fetch categories: %s", exc)
        return {}
    return {c["id"]: c["name"] for c in resp.json().get("children", [])
            if c.get("id") and c.get("name")}

def fetch_all_docs(session, token) -> list[Document]:
    logging.info("Fetching all docs…")
    category_map = fetch_job_categories(session, token)
    headers = {**COMMON_HEADERS, "X-AUTH-TOKEN": token}
    all_raw, docs = [], []
    page, start, limit = 1, 0, 50

    while True:
        params = {
            "_dc": str(int(time.time() * 1000)),
            "opts": json.dumps({"calculateStockConflicts": False}),
            "page": page, "start": start, "limit": limit,
            "sort": json.dumps([{"property": "startDate", "direction": "ASC"}]),
        }
        try:
            r = session.get(f"{EVENTWORX_BASE}/backend/job", headers=headers, params=params)
            r.raise_for_status()
        except requests.RequestException as exc:
            logging.exception("Failed page %s: %s", page, exc)
            break

        chunk = r.json()
        all_raw.append(chunk)
        rows = chunk.get("data", [])
        for e in rows:
            category_ids = e.get("categories", [])
            docs.append(Document(
                jobNumber=e.get("jobNumber", ""),
                projectNumber=e.get("projectNumber", ""),
                docType=e.get("docType", "").lower(),
                dealType=e.get("dealType") or "",
                title=e.get("title"),
                status=e.get("status") or "",
                activation=e.get("activation"),
                modificationDate=e.get("modificationDate"),
                overallPriceValue=(e.get("overallPriceValue") or 0) / 100.0
                                  if e.get("overallPriceValue") is not None else None,
                endDate=e.get("endDate"),
                rentStartDate=normalize_eventworx_datetime(e.get("rentStartDate")),
                rentEndDate=normalize_eventworx_datetime(e.get("rentEndDate")),
                jobCategoryNames=[category_map[cid] for cid in category_ids
                                  if cid in category_map],
            ))
        if len(rows) < limit:
            break
        page += 1
        start += limit

    with open("all_eventworx_raw.json", "w", encoding="utf-8") as f:
        json.dump(all_raw, f, ensure_ascii=False, indent=2)

    return docs


# --------------- NOTION HELPERS ---------------
notion = Client(auth=NOTION_TOKEN)

_last_notion_call: float = 0.0
_NOTION_INTERVAL = 1.0 / 3  # 3 req/s

def notion_call(func, *args, **kwargs):
    """Call a Notion API function, sleeping only the remaining time needed to stay within
    3 req/s, and retrying automatically on 429 with exponential backoff."""
    global _last_notion_call
    for attempt in range(6):
        gap = _NOTION_INTERVAL - (time.time() - _last_notion_call)
        if gap > 0:
            time.sleep(gap)
        _last_notion_call = time.time()
        try:
            return func(*args, **kwargs)
        except APIResponseError as e:
            if e.status == 429:
                wait = 2 ** attempt
                logging.warning("Rate limited, retrying in %ds…", wait)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Notion rate limit retries exhausted")

def resolve_notion_data_source_id() -> str:
    response = notion_call(notion.databases.retrieve, DATABASE_ID)
    data_sources = response.get("data_sources") or []
    if not data_sources:
        raise RuntimeError(f"No data sources found for Notion id {DATABASE_ID}")
    data_source_id = data_sources[0].get("id")
    if not data_source_id:
        raise RuntimeError(f"No data source id found for Notion id {DATABASE_ID}")
    return data_source_id

def get_nested(d, keys, default=None):
    for k in keys:
        if isinstance(d, list):
            if isinstance(k, int) and len(d) > k:
                d = d[k]
            else:
                return default
        elif isinstance(d, dict):
            d = d.get(k, default)
        else:
            return default
    return d if d is not None else default

def fetch_existing_notion_entries(data_source_id: str):
    existing = {}
    start_cursor = None
    while True:
        body = {"page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor
        resp = notion_call(notion.data_sources.query, data_source_id=data_source_id, **body)
        for page in resp["results"]:
            props = page.get("properties", {})
            pn = get_nested(props, ["Project Number", "rich_text", 0, "text", "content"], "").strip()
            if not pn:
                continue
            categories_raw = get_nested(props, ["Categories", "multi_select"], [])
            if not isinstance(categories_raw, list):
                categories_raw = []
            # Extract icon — stored as the URL (external/file) or emoji string so it's
            # cache-serializable. None means no icon is set on this page.
            icon_obj = page.get("icon") or {}
            icon_type = icon_obj.get("type")
            if icon_type == "emoji":
                icon_str = icon_obj.get("emoji")
            elif icon_type == "external":
                icon_str = get_nested(icon_obj, ["external", "url"])
            elif icon_type == "file":
                icon_str = get_nested(icon_obj, ["file", "url"])
            elif icon_type == "icon":
                inner = icon_obj.get("icon") or {}
                icon_str = f"icon:{inner.get('name')}:{inner.get('color')}"
            else:
                icon_str = None
            existing[pn] = {
                "page_id": page["id"],
                "obj": make_project_summary(
                    projectNumber=pn,
                    title=get_nested(props, ["Title", "title", 0, "text", "content"], ""),
                    status=get_nested(props, ["Status", "select", "name"], ""),
                    currentPrice=get_nested(props, ["Current Price", "number"], None),
                    rentStartDate=normalize_notion_datetime(get_nested(props, ["Rent", "date", "start"], None)),
                    rentEndDate=normalize_notion_datetime(get_nested(props, ["Rent", "date", "end"], None)),
                    representativeJob=get_nested(props, ["Representative Job", "rich_text", 0, "text", "content"], ""),
                    representativeDocType=get_nested(props, ["Representative Type", "select", "name"], ""),
                    categories=sorted([c.get("name") for c in categories_raw
                                       if isinstance(c, dict) and c.get("name")]),
                    has_order=get_nested(props, ["Has Order", "checkbox"], False),
                    has_offer=get_nested(props, ["Has Offer", "checkbox"], False),
                    has_request=get_nested(props, ["Has Request", "checkbox"], False),
                    has_delivery=get_nested(props, ["Has Delivery", "checkbox"], False),
                    has_invoice=get_nested(props, ["Has Invoice", "checkbox"], False),
                    icon=icon_str,
                )
            }
        if not resp.get("has_more"):
            break
        start_cursor = resp.get("next_cursor")
    return existing

def page_icon(p: ProjectSummary) -> dict | None:
    """Return the Notion icon for a project, or None to leave it unchanged."""
    if "Full Service" in p.categories:
        return {"type": "icon", "icon": {"name": "sliders-vertical", "color": "blue"}}
    if "Auf- und Abbau" in p.categories:
        return {"type": "icon", "icon": {"name": "wrench", "color": "blue"}}
    if "Technikmiete" in p.categories:
        return {"type": "icon", "icon": {"name": "swap-horizontally", "color": "blue"}}
    return None

def build_notion_props(p: ProjectSummary) -> dict:
    props = {
        "Title":               {"title": [{"text": {"content": p.title or "Untitled"}}]},
        "Project Number":      {"rich_text": [{"text": {"content": p.projectNumber}}]},
        "Status":              {"select": {"name": p.status or ""}},
        "Representative Job":  {"rich_text": [{"text": {"content": p.representativeJob}}]},
        "Representative Type": {"select": {"name": p.representativeDocType}},
        "Has Order":    {"checkbox": p.has_order},
        "Has Offer":    {"checkbox": p.has_offer},
        "Has Request":  {"checkbox": p.has_request},
        "Has Delivery": {"checkbox": p.has_delivery},
        "Has Invoice":  {"checkbox": p.has_invoice},
    }
    if p.currentPrice is not None:
        props["Current Price"] = {"number": float(p.currentPrice)}
    if p.rentStartDate or p.rentEndDate:
        props["Rent"] = {"date": {"start": p.rentStartDate, "end": p.rentEndDate}}
    if p.categories:
        props["Categories"] = {"multi_select": [{"name": c} for c in p.categories]}
    return props

def load_local() -> dict[str, ProjectSummary]:
    if not os.path.exists(LOCAL_CACHE):
        return {}
    with open(LOCAL_CACHE, "r", encoding="utf-8") as f:
        items = json.load(f)
    projects = {}
    for p in items:
        if not isinstance(p.get("categories"), list):
            p["categories"] = []
        projects[p["projectNumber"]] = ProjectSummary(**p)
    return projects

def save_local(projects: list[ProjectSummary]):
    with open(LOCAL_CACHE, "w", encoding="utf-8") as f:
        json.dump([asdict(p) for p in projects], f, ensure_ascii=False, indent=2)

def meaningful_diffs(old_proj: ProjectSummary, new_proj: ProjectSummary) -> dict:
    """Return only diffs where the new value is non-empty/non-null.
    Prevents clearing existing Notion values when Eventworx returns empty data."""
    old, new = asdict(old_proj), asdict(new_proj)
    diffs = {}
    for k in old:
        o, n = old.get(k), new.get(k)
        if o == n:
            continue
        if n is None:
            continue
        if isinstance(n, list) and not n:
            continue
        if isinstance(n, str) and not n.strip():
            continue
        diffs[k] = {"old": o, "new": n}
    return diffs

def save_notion_local(existing: dict):
    with open("local_notion_projects.json", "w", encoding="utf-8") as f:
        json.dump([asdict(e["obj"]) for e in existing.values()], f, ensure_ascii=False, indent=2)


# ---------------- MAIN ----------------
def main():
    session = auth_token = None
    # Set to False to skip Notion sync when local cache shows no changes
    
    try:
        session, auth_token = try_login(USERNAME, PASSWORD)
        all_docs = fetch_all_docs(session, auth_token)
        logout(session, auth_token)
        session = auth_token = None

        logging.info("Aggregating by project…")
        projects = aggregate_projects(all_docs)

        data_source_id = resolve_notion_data_source_id()
        existing = fetch_existing_notion_entries(data_source_id)
        save_notion_local(existing)

        logging.info("Syncing %d projects with Notion…", len(projects))
        for p in projects:
            props = build_notion_props(p)
            if p.projectNumber in existing:
                entry = existing[p.projectNumber]
                mdiffs = meaningful_diffs(entry["obj"], p)
                # Set icon only if none is set yet — don't overwrite manually chosen icons.
                desired_icon = page_icon(p) if entry["obj"].icon is None else None
                if force_notion_sync or mdiffs or desired_icon:
                    notion_call(notion.pages.update, page_id=entry["page_id"],
                                properties=props,
                                **({"icon": desired_icon} if desired_icon else {}))
                    logging.info("Updated  %s (%s)", p.projectNumber, p.representativeJob)
                    logging.debug("  Changes: %s", mdiffs)
                else:
                    logging.info("Unchanged %s", p.projectNumber)
            else:
                # New page — no existing icon, set one if the category warrants it.
                icon = page_icon(p)
                notion_call(notion.pages.create, parent={"data_source_id": data_source_id},
                            properties=props,
                            **({"icon": icon} if icon else {}))
                logging.info("Created  %s (%s)", p.projectNumber, p.representativeJob)

        save_local(projects)
    finally:
        if session and auth_token:
            logout(session, auth_token)

if __name__ == "__main__":
    main()
