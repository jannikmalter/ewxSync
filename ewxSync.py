#!/usr/bin/env python3
from dataclasses import dataclass, asdict
import time, json, logging, requests, sys, os, re, signal
from datetime import datetime, timezone
from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError

load_dotenv()

force_notion_sync = False
# When True, log every Discord write but make no API call (channel topics + thread sync).
dry_run = False


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

# -------- CONFIG --------
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID  = os.getenv("DATABASE_ID")   

EVENTWORX_BASE = os.getenv("EVENTWORX_BASE")
LOCAL_PROJECTS_CACHE = "local_eventworx_projects.json"
LOCAL_DOCS_CACHE = "local_eventworx_docs.json"

USERNAME = os.getenv("EVENTWORX_USERNAME")
PASSWORD = os.getenv("EVENTWORX_PASSWORD")

DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DISCORD_VERMIETUNGEN_CHANNEL_ID = os.getenv("DISCORD_VERMIETUNGEN_CHANNEL_ID")
DISCORD_JOBS_CATEGORY_ID = os.getenv("DISCORD_JOBS_CATEGORY_ID")
DISCORD_CREW_ROLE_ID = os.getenv("DISCORD_CREW_ROLE_ID")
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_CACHE    = "local_discord_channels.json"
DISCORD_THREADS_CACHE = "local_discord_threads.json"

# Daemon configuration
POLL_INTERVAL_SECONDS = 60         # how often to probe Eventworx for changes
FULL_SYNC_INTERVAL_SECONDS = 3600  # run an unconditional full sync at least this often
SYNC_STATE_FILE = "sync_state.json"

# Thread names start with the project number so we can join threads to projects
# without persisting any IDs: e.g. "P-1234_Sommerfest Müller".
THREAD_NAME_MAX = 100  # Discord channel/thread name limit
THREAD_AUTO_ARCHIVE_MINUTES = 10080  # 7 days — Discord allows 60/1440/4320/10080
CHANNEL_NAME_MAX = 100  # Discord channel name limit

_EWX_TAG_RE = re.compile(r'\[EWX:(P-\d+)\](?:\(([^)]+)\))?')
_NOTION_TAG_RE = re.compile(r'\[Notion\]\(([^)]+)\)')
_THREAD_PREFIX_RE = re.compile(r'^(P-\d+)_')

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
    docId: str = ""
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
    representativeUrl: str | None = None
    notionUrl: str | None = None  # read-only — never written back to Notion, never diffed

    def __post_init__(self):
        if self.categories is None:
            self.categories = []
        if not isinstance(self.categories, list):
            self.categories = []

@dataclass
class DiscordChannel:
    channelId: str
    channelName: str
    projectNumber: str
    eventworxUrl: str | None = None
    notionUrl: str | None = None
    topic: str | None = None

@dataclass
class DiscordThread:
    threadId: str
    threadName: str
    projectNumber: str
    archived: bool = False


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
                         has_delivery=False, has_invoice=False, icon=None,
                         representativeUrl=None, notionUrl=None):
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
        representativeUrl=representativeUrl or None,
        notionUrl=notionUrl or None,
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


def aggregate_one_project(pn: str, docs: list[Document], now_ms: int | None = None) -> ProjectSummary:
    """Aggregate a single project's docs into a ProjectSummary.

    Factored out of aggregate_projects so the incremental tick can re-aggregate
    only the projects whose docs changed, without walking the entire cache.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    status, rep = classify_project(docs, now_ms)
    doc_types = {d.docType for d in docs}

    # Merge categories across all docs — they can be set on any document type,
    # not just the representative one.
    all_cats = sorted({n for d in docs for n in (d.jobCategoryNames or []) if n})

    # Prefer the most recent non-cancelled, non-archived invoice's price when one exists.
    # Invoices reflect the actually-billed amount (including additional services that
    # may have been added after the order was placed).
    invoices = [d for d in docs
                if d.docType == "invoice"
                and d.status not in CANCELLED_STATUSES
                and d.activation != "archived"
                and d.overallPriceValue is not None]
    if invoices:
        price = max(invoices, key=lambda d: d.modificationDate or 0).overallPriceValue
    else:
        price = rep.overallPriceValue if rep.overallPriceValue is not None else _first_non_null(docs, "overallPriceValue")
    rent_start = rep.rentStartDate or _first_non_null(docs, "rentStartDate")
    rent_end   = rep.rentEndDate   or _first_non_null(docs, "rentEndDate")

    rep_url = (f"{EVENTWORX_BASE}/eventworx/#job/edit/{rep.docType}/{rep.docId}"
               if rep.docId else None)

    return ProjectSummary(
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
        representativeUrl=rep_url,
    )


def group_docs_by_project(all_docs: list[Document]) -> dict[str, list[Document]]:
    by_project: dict[str, list[Document]] = {}
    for d in all_docs:
        if d.projectNumber:
            by_project.setdefault(d.projectNumber, []).append(d)
    return by_project


def aggregate_projects(all_docs: list[Document]) -> list[ProjectSummary]:
    now_ms = int(time.time() * 1000)
    by_project = group_docs_by_project(all_docs)
    return [aggregate_one_project(pn, docs, now_ms) for pn, docs in by_project.items()]


# --------------- EVENTWORX API ---------------
def _log_http(label: str, resp: requests.Response):
    """Single-line per-request log: status, body size, server-side elapsed time.

    `resp.elapsed` covers from sending the request to receiving the response
    headers — close enough to measure backend latency without timing the body read.
    """
    logging.info("%s: HTTP %d  %d B  %d ms",
                 label, resp.status_code, len(resp.content),
                 int(resp.elapsed.total_seconds() * 1000))


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
    headers = {**COMMON_HEADERS, "X-AUTH-TOKEN": token}
    params = {"_dc": str(int(time.time() * 1000)), "withoutIndex": "true"}
    try:
        resp = session.get(f"{EVENTWORX_BASE}/backend/category/tree/job/groups/rent",
                           headers=headers, params=params)
        _log_http("EWX categories", resp)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logging.exception("Failed to fetch categories: %s", exc)
        return {}
    return {c["id"]: c["name"] for c in resp.json().get("children", [])
            if c.get("id") and c.get("name")}

def _parse_doc_row(e: dict, category_map: dict) -> Document:
    """Build a Document from a single /backend/job response row.

    Shared by full fetch (fetch_all_docs) and the incremental change probe
    (fetch_changed_docs) so they're guaranteed to produce identical records.
    """
    category_ids = e.get("categories", [])
    return Document(
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
        docId=e.get("id", ""),
        jobCategoryNames=[category_map[cid] for cid in category_ids if cid in category_map],
    )


def fetch_all_docs(session, token, category_map: dict) -> list[Document]:
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
            _log_http(f"EWX all_docs page {page}", r)
            r.raise_for_status()
        except requests.RequestException as exc:
            logging.exception("Failed page %s: %s", page, exc)
            break

        chunk = r.json()
        all_raw.append(chunk)
        rows = chunk.get("data", [])
        for e in rows:
            docs.append(_parse_doc_row(e, category_map))
        if len(rows) < limit:
            break
        page += 1
        start += limit

    with open("all_eventworx_raw.json", "w", encoding="utf-8") as f:
        json.dump(all_raw, f, ensure_ascii=False, indent=2)

    return docs


def fetch_changed_docs(session, token, since_ms: int, category_map: dict) -> list[Document]:
    """Return all docs with `lastModification > since_ms`, paginated.

    Drives the incremental tick: instead of probing for a count and then re-fetching
    everything, we fetch only the docs that actually changed. Each row carries all
    fields needed to update the cached Document in place.

    `category_map` is passed in by the caller (kept in SyncState and refreshed
    only on full syncs) so incremental ticks don't refetch the categories tree.
    Stale entries are harmless — any unknown category id is simply dropped from
    `jobCategoryNames`, and the hourly full sync brings the map up to date.

    The Solr field is `lastModification`; the response body exposes it as
    `modificationDate`. See `eventworx API analysis.md`.
    """
    headers = {**COMMON_HEADERS, "X-AUTH-TOKEN": token}
    docs: list[Document] = []
    page, start, limit = 1, 0, 50

    while True:
        params = {
            "_dc": str(int(time.time() * 1000)),
            "opts": json.dumps({"calculateStockConflicts": False}),
            "page": page, "start": start, "limit": limit,
            "sort":   json.dumps([{"property": "lastModification", "direction": "ASC"}]),
            "filter": json.dumps([
                {"property": "lastModification|*", "operator": ">", "value": since_ms},
            ]),
        }
        r = session.get(f"{EVENTWORX_BASE}/backend/job", headers=headers, params=params)
        _log_http(f"EWX changed_docs page {page} (since={since_ms})", r)
        r.raise_for_status()
        chunk = r.json()
        rows = chunk.get("data", [])
        for e in rows:
            docs.append(_parse_doc_row(e, category_map))
        total = chunk.get("total")
        if total is not None and start + len(rows) >= int(total):
            break
        if len(rows) < limit:
            break
        page += 1
        start += limit

    return docs


# --------------- NOTION HELPERS ---------------
notion = Client(auth=NOTION_TOKEN)

_last_notion_call: float = 0.0
_NOTION_INTERVAL = 1.0 / 3  # 3 req/s

def notion_call(func, *args, **kwargs):
    """Call a Notion API function, sleeping only the remaining time needed to stay within
    3 req/s, and retrying automatically on 429 with exponential backoff."""
    global _last_notion_call
    label = getattr(func, "__qualname__", None) or getattr(func, "__name__", "?")
    for attempt in range(6):
        gap = _NOTION_INTERVAL - (time.time() - _last_notion_call)
        if gap > 0:
            time.sleep(gap)
        _last_notion_call = time.time()
        t0 = time.monotonic()
        try:
            result = func(*args, **kwargs)
        except APIResponseError as e:
            if e.status == 429:
                wait = 2 ** attempt
                logging.warning("Rate limited, retrying in %ds…", wait)
                time.sleep(wait)
                continue
            raise
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        # Response size is approximated by re-serializing the parsed dict — the
        # notion-client doesn't expose the raw response, so this is the closest
        # equivalent to len(http_response.content). Negligible CPU cost.
        try:
            size = len(json.dumps(result, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            size = -1
        logging.info("Notion %s: %d B  %d ms", label, size, elapsed_ms)
        return result
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

def extract_notion_entry(page: dict) -> tuple[str, dict] | None:
    """Extract `(projectNumber, entry)` from a Notion page.

    Returns None if the page has no Project Number — those rows are user-created
    placeholders we don't manage. Entry shape mirrors fetch_existing_notion_entries.
    """
    props = page.get("properties", {})
    pn = get_nested(props, ["Project Number", "rich_text", 0, "text", "content"], "").strip()
    if not pn:
        return None
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
    entry = {
        "page_id": page["id"],
        "last_edited_time": page.get("last_edited_time"),
        "obj": make_project_summary(
            projectNumber=pn,
            title=get_nested(props, ["Title", "title", 0, "text", "content"], ""),
            status=get_nested(props, ["Status", "select", "name"], ""),
            currentPrice=get_nested(props, ["Current Price", "number"], None),
            rentStartDate=normalize_notion_datetime(get_nested(props, ["Rent", "date", "start"], None)),
            rentEndDate=normalize_notion_datetime(get_nested(props, ["Rent", "date", "end"], None)),
            representativeJob=get_nested(props, ["Representative Job", "rich_text", 0, "text", "content"], ""),
            representativeUrl=get_nested(props, ["Project Number", "rich_text", 0, "text", "link", "url"], None),
            representativeDocType=get_nested(props, ["Representative Type", "select", "name"], ""),
            categories=sorted([c.get("name") for c in categories_raw
                               if isinstance(c, dict) and c.get("name")]),
            has_order=get_nested(props, ["Has Order", "checkbox"], False),
            has_offer=get_nested(props, ["Has Offer", "checkbox"], False),
            has_request=get_nested(props, ["Has Request", "checkbox"], False),
            has_delivery=get_nested(props, ["Has Delivery", "checkbox"], False),
            has_invoice=get_nested(props, ["Has Invoice", "checkbox"], False),
            icon=icon_str,
            notionUrl="https://www.notion.so/" + page["id"].replace("-", ""),
        ),
    }
    return pn, entry


def fetch_existing_notion_entries(data_source_id: str):
    existing = {}
    start_cursor = None
    while True:
        body = {"page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor
        resp = notion_call(notion.data_sources.query, data_source_id=data_source_id, **body)
        for page in resp["results"]:
            extracted = extract_notion_entry(page)
            if extracted is None:
                continue
            pn, entry = extracted
            existing[pn] = entry
        if not resp.get("has_more"):
            break
        start_cursor = resp.get("next_cursor")
    return existing


def fetch_changed_notion_pages(data_source_id: str, since_iso: str) -> list[tuple[str, dict]]:
    """Return Notion pages whose `last_edited_time` is >= since_iso, paginated.

    `since_iso` is inclusive (Notion's `on_or_after`); the caller decides whether
    to advance the checkpoint exclusively. Includes pages we just wrote ourselves —
    that's intentional: the cache mirrors Notion, so we re-pull our own writes to
    keep cache in sync. Re-aggregation against EWX will then yield no diff.
    """
    changed: list[tuple[str, dict]] = []
    start_cursor = None
    while True:
        body = {
            "page_size": 100,
            "filter": {
                "timestamp": "last_edited_time",
                "last_edited_time": {"on_or_after": since_iso},
            },
            "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
        }
        if start_cursor:
            body["start_cursor"] = start_cursor
        resp = notion_call(notion.data_sources.query, data_source_id=data_source_id, **body)
        for page in resp["results"]:
            extracted = extract_notion_entry(page)
            if extracted is not None:
                changed.append(extracted)
        if not resp.get("has_more"):
            break
        start_cursor = resp.get("next_cursor")
    return changed

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
        "Project Number":      {"rich_text": [{"text": {
            "content": p.projectNumber,
            **({"link": {"url": p.representativeUrl}} if p.representativeUrl else {}),
        }}]},
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

def load_local_projects() -> dict[str, ProjectSummary]:
    """Load the aggregated ProjectSummary snapshot.

    Used to bootstrap the in-memory project state at startup so an incremental
    tick has a baseline to push from even before the next full sync. The raw
    docs cache is the authoritative input to aggregation; this is the derived view.
    """
    if not os.path.exists(LOCAL_PROJECTS_CACHE):
        return {}
    with open(LOCAL_PROJECTS_CACHE, "r", encoding="utf-8") as f:
        items = json.load(f)
    projects = {}
    for p in items:
        if not isinstance(p.get("categories"), list):
            p["categories"] = []
        projects[p["projectNumber"]] = ProjectSummary(**p)
    return projects

def save_local_projects(projects: list[ProjectSummary]):
    with open(LOCAL_PROJECTS_CACHE, "w", encoding="utf-8") as f:
        json.dump([asdict(p) for p in projects], f, ensure_ascii=False, indent=2)

def load_local_docs() -> dict[str, dict[str, Document]]:
    """Load raw EWX docs grouped by projectNumber, keyed by `(docType, docId)`.

    This cache is the authoritative input to per-project re-aggregation on
    incremental ticks. When the probe returns a changed doc, we replace the
    matching entry in `docs[pn][key]` without re-fetching the rest of the project.
    """
    if not os.path.exists(LOCAL_DOCS_CACHE):
        return {}
    with open(LOCAL_DOCS_CACHE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    out: dict[str, dict[str, Document]] = {}
    for pn, docs in raw.items():
        out[pn] = {}
        for d in docs:
            doc = Document(**d)
            out[pn][_doc_key(doc)] = doc
    return out

def save_local_docs(docs_by_pn: dict[str, dict[str, Document]]):
    serializable = {pn: [asdict(d) for d in docs.values()]
                    for pn, docs in docs_by_pn.items()}
    with open(LOCAL_DOCS_CACHE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)

def _doc_key(d: Document) -> str:
    """Stable identity for a Document within a project.

    docId is the EWX UUID — unique across the entire system, so this alone is
    sufficient. Including docType makes log lines and debug dumps readable.
    """
    return f"{d.docType}:{d.docId}"

def load_sync_state() -> dict:
    if not os.path.exists(SYNC_STATE_FILE):
        return {}
    try:
        with open(SYNC_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def save_sync_state(state: dict):
    with open(SYNC_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

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

def save_notion_local_dict(existing: dict):
    """Persist the Notion cache including page_id and last_edited_time.

    Stored as a flat list (not a dict keyed by projectNumber) so the file remains
    human-readable and easy to diff between runs.
    """
    serializable = [{"page_id": e["page_id"],
                     "last_edited_time": e.get("last_edited_time"),
                     "obj": asdict(e["obj"])} for e in existing.values()]
    with open("local_notion_projects.json", "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)


def load_notion_local() -> dict[str, dict]:
    """Load the Notion cache. Empty when the file is missing or pre-refactor format.

    Pre-refactor files contained only ProjectSummary records (no page_id), which
    are unusable as a Notion mirror. We return empty for those — the next full
    sync repopulates from Notion directly.
    """
    if not os.path.exists("local_notion_projects.json"):
        return {}
    try:
        with open("local_notion_projects.json", "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict) or "page_id" not in item or "obj" not in item:
            return {}  # legacy format, force a fresh fetch
        obj = item["obj"]
        if not isinstance(obj.get("categories"), list):
            obj["categories"] = []
        pn = obj.get("projectNumber")
        if not pn:
            continue
        out[pn] = {
            "page_id": item["page_id"],
            "last_edited_time": item.get("last_edited_time"),
            "obj": ProjectSummary(**obj),
        }
    return out


# --------------- DISCORD ---------------
def fetch_discord_channels() -> list[DiscordChannel]:
    logging.info("Fetching Discord channels…")
    headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
    r = requests.get(f"{DISCORD_API_BASE}/guilds/{DISCORD_GUILD_ID}/channels", headers=headers)
    r.raise_for_status()
    channels = []
    for ch in r.json():
        if ch.get("type") != 0:          # text channels only
            continue
        topic = ch.get("topic") or ""
        m = _EWX_TAG_RE.search(topic)
        if not m:
            continue
        notion_m = _NOTION_TAG_RE.search(topic)
        channels.append(DiscordChannel(
            channelId=str(ch["id"]),
            channelName=ch["name"],
            projectNumber=m.group(1),
            eventworxUrl=m.group(2) or None,
            notionUrl=notion_m.group(1) if notion_m else None,
            topic=topic,
        ))
    logging.info("Found %d Discord channels with EWX tags.", len(channels))
    return channels

def save_discord_local(channels: list[DiscordChannel]):
    with open(DISCORD_CACHE, "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in channels], f, ensure_ascii=False, indent=2)

def load_discord_local() -> list[DiscordChannel]:
    if not os.path.exists(DISCORD_CACHE):
        return []
    with open(DISCORD_CACHE, "r", encoding="utf-8") as f:
        return [DiscordChannel(**c) for c in json.load(f)]

def build_discord_topic(old_topic: str | None, project_number: str,
                        ewx_url: str | None, notion_url: str | None) -> str:
    """Rebuild a topic with up-to-date EWX and Notion tags.

    The EWX tag is always rewritten in place. The Notion tag is updated in place if
    present, otherwise inserted directly after the EWX tag. Surrounding free-form
    topic text is preserved.
    """
    topic = old_topic or ""
    new_ewx_tag = (f"[EWX:{project_number}]({ewx_url})" if ewx_url
                   else f"[EWX:{project_number}]")
    topic = _EWX_TAG_RE.sub(lambda _: new_ewx_tag, topic, count=1)

    if notion_url:
        new_notion_tag = f"[Notion]({notion_url})"
        if _NOTION_TAG_RE.search(topic):
            topic = _NOTION_TAG_RE.sub(new_notion_tag, topic, count=1)
        else:
            topic = topic.replace(new_ewx_tag, f"{new_ewx_tag} {new_notion_tag}", 1)
    return topic


def update_discord_topic(channel_id: str, new_topic: str):
    headers = {"Authorization": f"Bot {DISCORD_TOKEN}",
               "Content-Type": "application/json"}
    r = requests.patch(f"{DISCORD_API_BASE}/channels/{channel_id}",
                       headers=headers, json={"topic": new_topic})
    r.raise_for_status()


# --------------- DISCORD THREADS (vermietungen) ---------------
def _discord_headers() -> dict:
    return {"Authorization": f"Bot {DISCORD_TOKEN}",
            "Content-Type": "application/json"}

def _parse_thread(raw: dict) -> DiscordThread | None:
    name = raw.get("name") or ""
    m = _THREAD_PREFIX_RE.match(name)
    if not m:
        return None
    meta = raw.get("thread_metadata") or {}
    return DiscordThread(
        threadId=str(raw["id"]),
        threadName=name,
        projectNumber=m.group(1),
        archived=bool(meta.get("archived", False)),
    )

def fetch_active_threads(channel_id: str) -> list[DiscordThread]:
    """Return non-archived threads in the given channel whose name starts with P-XXXX_."""
    r = requests.get(f"{DISCORD_API_BASE}/guilds/{DISCORD_GUILD_ID}/threads/active",
                     headers=_discord_headers())
    r.raise_for_status()
    threads = []
    for raw in r.json().get("threads", []):
        if str(raw.get("parent_id")) != str(channel_id):
            continue
        t = _parse_thread(raw)
        if t:
            threads.append(t)
    logging.info("Found %d active vermietungen threads.", len(threads))
    return threads

def fetch_archived_threads(channel_id: str) -> list[DiscordThread]:
    """Return archived public threads in the given channel with a P-XXXX_ prefix.
    Paginates via the `before` timestamp until `has_more` is false."""
    headers = _discord_headers()
    threads, before = [], None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        r = requests.get(f"{DISCORD_API_BASE}/channels/{channel_id}/threads/archived/public",
                         headers=headers, params=params)
        r.raise_for_status()
        body = r.json()
        page = body.get("threads", [])
        for raw in page:
            t = _parse_thread(raw)
            if t:
                threads.append(t)
        if not body.get("has_more") or not page:
            break
        # Page by the oldest archive_timestamp in this page.
        last = page[-1].get("thread_metadata", {}).get("archive_timestamp")
        if not last:
            break
        before = last
    return threads

def build_thread_name(project_number: str, title: str) -> str:
    title = (title or "").strip() or "Untitled"
    name = f"{project_number}_{title}"
    if len(name) > THREAD_NAME_MAX:
        name = name[:THREAD_NAME_MAX]
    return name

def create_thread(channel_id: str, name: str) -> DiscordThread | None:
    """Create a public thread without a starter message. Returns the parsed thread or None."""
    payload = {
        "name": name,
        "type": 11,  # PUBLIC_THREAD
        "auto_archive_duration": THREAD_AUTO_ARCHIVE_MINUTES,
    }
    r = requests.post(f"{DISCORD_API_BASE}/channels/{channel_id}/threads",
                      headers=_discord_headers(), json=payload)
    r.raise_for_status()
    return _parse_thread(r.json())

def ping_crew_in_thread(thread_id: str):
    """Post a role mention so all Crew members get auto-added to the thread."""
    if not DISCORD_CREW_ROLE_ID:
        logging.warning("DISCORD_CREW_ROLE_ID not set — skipping crew ping.")
        return
    payload = {
        "content": f"<@&{DISCORD_CREW_ROLE_ID}>",
        "allowed_mentions": {"parse": [], "roles": [DISCORD_CREW_ROLE_ID]},
    }
    r = requests.post(f"{DISCORD_API_BASE}/channels/{thread_id}/messages",
                      headers=_discord_headers(), json=payload)
    r.raise_for_status()

def archive_thread(thread_id: str):
    r = requests.patch(f"{DISCORD_API_BASE}/channels/{thread_id}",
                       headers=_discord_headers(), json={"archived": True})
    r.raise_for_status()

def unarchive_thread(thread_id: str, name: str | None = None):
    """Unarchive a thread, optionally renaming it in the same PATCH."""
    payload: dict = {"archived": False}
    if name is not None:
        payload["name"] = name
    r = requests.patch(f"{DISCORD_API_BASE}/channels/{thread_id}",
                       headers=_discord_headers(), json=payload)
    r.raise_for_status()

def rename_thread(thread_id: str, name: str):
    r = requests.patch(f"{DISCORD_API_BASE}/channels/{thread_id}",
                       headers=_discord_headers(), json={"name": name})
    r.raise_for_status()

def save_discord_threads_local(threads: list[DiscordThread]):
    with open(DISCORD_THREADS_CACHE, "w", encoding="utf-8") as f:
        json.dump([asdict(t) for t in threads], f, ensure_ascii=False, indent=2)


def build_channel_name(p: ProjectSummary) -> str:
    """Channel name = 'YYMMDD title' using the project's rent start date.
    Falls back to just the title when no rent start is set."""
    title = (p.title or "").strip() or f"Project {p.projectNumber}"
    prefix = ""
    if p.rentStartDate:
        try:
            dt = datetime.fromisoformat(p.rentStartDate)
            prefix = dt.strftime("%y%m%d") + " "
        except (ValueError, TypeError):
            prefix = ""
    name = f"{prefix}{title}"
    if len(name) > CHANNEL_NAME_MAX:
        name = name[:CHANNEL_NAME_MAX]
    return name

def create_discord_channel(name: str, topic: str, parent_id: str) -> DiscordChannel | None:
    """Create a text channel under parent_id with a pre-populated topic."""
    payload = {"name": name, "type": 0, "topic": topic, "parent_id": parent_id}
    r = requests.post(f"{DISCORD_API_BASE}/guilds/{DISCORD_GUILD_ID}/channels",
                      headers=_discord_headers(), json=payload)
    r.raise_for_status()
    raw = r.json()
    m = _EWX_TAG_RE.search(raw.get("topic") or "")
    if not m:
        return None
    return DiscordChannel(
        channelId=str(raw["id"]),
        channelName=raw.get("name", name),
        projectNumber=m.group(1),
        eventworxUrl=m.group(2) or None,
        topic=raw.get("topic"),
    )


def sync_job_channels(projects: list[ProjectSummary],
                      discord_by_project: dict[str, DiscordChannel]) -> list[DiscordChannel]:
    """Create a Discord channel for each active non-Technikmiete project that lacks one.

    Newly created channels are returned and also inserted into `discord_by_project` so the
    subsequent per-project topic-update pass treats them as already-discovered.
    No archive/move when a project leaves the target set — channels are kept manually.
    """
    prefix = "[DRY-RUN] " if dry_run else ""
    logging.info("%sChecking job channels (Aktiv, non-Technikmiete)…", prefix)

    if not DISCORD_JOBS_CATEGORY_ID:
        logging.warning("DISCORD_JOBS_CATEGORY_ID not set — skipping job channel creation.")
        return []

    target = [p for p in projects
              if p.status == "Aktiv" and "Technikmiete" not in (p.categories or [])]
    missing = [p for p in target if p.projectNumber not in discord_by_project]

    logging.info("%sJob channels: %d target projects, %d already have a channel, %d to create.",
                 prefix, len(target), len(target) - len(missing), len(missing))

    if not target:
        logging.info("%sNo active non-Technikmiete projects — nothing to create.", prefix)
        return []
    if not missing:
        logging.info("%sAll %d target projects already have a Discord channel — nothing to create.",
                     prefix, len(target))
        return []

    created: list[DiscordChannel] = []
    for p in missing:
        name = build_channel_name(p)
        ewx_tag = (f"[EWX:{p.projectNumber}]({p.representativeUrl})"
                   if p.representativeUrl else f"[EWX:{p.projectNumber}]")
        if dry_run:
            logging.info("[DRY-RUN] Would create channel %s (%s) under category %s with topic %r",
                         p.projectNumber, name, DISCORD_JOBS_CATEGORY_ID, ewx_tag)
            continue
        ch = create_discord_channel(name, ewx_tag, DISCORD_JOBS_CATEGORY_ID)
        if ch:
            discord_by_project[p.projectNumber] = ch
            created.append(ch)
            logging.info("Created channel %s (%s)", p.projectNumber, name)

    if dry_run:
        logging.info("[DRY-RUN] Job channels: would create %d.", len(missing))
    else:
        logging.info("Job channels: created %d.", len(created))
    return created


def sync_vermietungen_threads(projects: list[ProjectSummary]):
    """Reconcile threads in the vermietungen channel against Aktiv+Technikmiete projects.

    - Project in target set, no active thread → unarchive an existing one if found, else create.
    - Active thread, project not in target set → archive.
    - Active thread in target set with stale name → rename.
    """
    prefix = "[DRY-RUN] " if dry_run else ""
    logging.info("%sChecking vermietungen threads (Aktiv + Technikmiete)…", prefix)

    if not DISCORD_VERMIETUNGEN_CHANNEL_ID:
        logging.warning("DISCORD_VERMIETUNGEN_CHANNEL_ID not set — skipping thread sync.")
        return

    channel_id = DISCORD_VERMIETUNGEN_CHANNEL_ID
    target = {p.projectNumber: p for p in projects
              if p.status == "Aktiv" and "Technikmiete" in (p.categories or [])}
    logging.info("%sThread target set: %d Aktiv+Technikmiete projects.", prefix, len(target))

    active = fetch_active_threads(channel_id)
    active_by_pn: dict[str, DiscordThread] = {}
    for t in active:
        # Defensively keep the first thread we see for a given PN; log if duplicates exist.
        if t.projectNumber in active_by_pn:
            logging.warning("Duplicate active thread for %s: %s and %s",
                            t.projectNumber, active_by_pn[t.projectNumber].threadName, t.threadName)
            continue
        active_by_pn[t.projectNumber] = t

    archived_by_pn: dict[str, DiscordThread] | None = None
    def archived_index() -> dict[str, DiscordThread]:
        nonlocal archived_by_pn
        if archived_by_pn is None:
            archived = fetch_archived_threads(channel_id)
            archived_by_pn = {}
            for t in archived:
                # Keep the most recently archived (first in API response) per PN.
                archived_by_pn.setdefault(t.projectNumber, t)
        return archived_by_pn

    final_state: dict[str, DiscordThread] = {}

    # Plan summary (computed before mutations so dry-run shows the same totals as a real run).
    plan_rename = sum(1 for pn, p in target.items()
                      if pn in active_by_pn
                      and active_by_pn[pn].threadName != build_thread_name(pn, p.title))
    plan_missing = [pn for pn in target if pn not in active_by_pn]
    plan_archive = sum(1 for pn in active_by_pn if pn not in target)
    if not (plan_missing or plan_rename or plan_archive):
        logging.info("%sThreads: all aligned — nothing to do.", prefix)
    else:
        logging.info("%sThreads plan: %d missing (create or unarchive), %d to rename, %d to archive.",
                     prefix, len(plan_missing), plan_rename, plan_archive)

    # Pass 1: ensure each target project has an active thread with the right name.
    for pn, p in target.items():
        desired_name = build_thread_name(pn, p.title)
        existing = active_by_pn.get(pn)
        if existing:
            if existing.threadName != desired_name:
                if dry_run:
                    logging.info("[DRY-RUN] Would rename thread %s: %r → %r",
                                 pn, existing.threadName, desired_name)
                else:
                    rename_thread(existing.threadId, desired_name)
                    existing.threadName = desired_name
                    logging.info("Renamed thread %s → %s", pn, desired_name)
            final_state[pn] = existing
            continue

        archived_match = archived_index().get(pn)
        if archived_match:
            name_arg = desired_name if archived_match.threadName != desired_name else None
            if dry_run:
                logging.info("[DRY-RUN] Would unarchive thread %s (%s)%s",
                             pn, archived_match.threadName,
                             f" and rename to {desired_name!r}" if name_arg else "")
            else:
                unarchive_thread(archived_match.threadId, name_arg)
                archived_match.archived = False
                if name_arg:
                    archived_match.threadName = name_arg
                final_state[pn] = archived_match
                logging.info("Unarchived thread %s (%s)", pn, archived_match.threadName)
            continue

        if dry_run:
            logging.info("[DRY-RUN] Would create thread %s (%s)", pn, desired_name)
            logging.info("[DRY-RUN] Would ping crew in thread %s", pn)
        else:
            created = create_thread(channel_id, desired_name)
            if created:
                final_state[pn] = created
                logging.info("Created thread %s (%s)", pn, desired_name)
                ping_crew_in_thread(created.threadId)

    # Pass 2: archive active threads whose project is no longer in the target set.
    for pn, t in active_by_pn.items():
        if pn in target:
            continue
        if dry_run:
            logging.info("[DRY-RUN] Would archive thread %s (%s)", pn, t.threadName)
        else:
            archive_thread(t.threadId)
            t.archived = True
            final_state[pn] = t
            logging.info("Archived thread %s (%s)", pn, t.threadName)

    if dry_run:
        logging.info("[DRY-RUN] Skipping save of %s.", DISCORD_THREADS_CACHE)
    else:
        save_discord_threads_local(list(final_state.values()))


# ---------------- SYNC PIPELINE ----------------
@dataclass
class SyncState:
    """In-memory mirror of EWX, Notion, and Discord state.

    Mutated in place by full_sync / incremental_sync. Persisted to disk after
    every successful tick so a daemon restart can resume without a full fetch.
    """
    docs_by_pn: dict[str, dict[str, Document]]       # raw EWX docs (authoritative input)
    projects: dict[str, ProjectSummary]              # derived view of docs_by_pn
    notion_entries: dict[str, dict]                  # mirror of Notion {pn: {"page_id", "last_edited_time", "obj"}}
    discord_by_pn: dict[str, DiscordChannel]         # mirror of Discord channels with EWX tags
    data_source_id: str                              # cached Notion data source id
    category_map: dict[str, str]                     # EWX category id → name, refreshed on full sync


def load_sync_state_from_disk(data_source_id: str) -> SyncState:
    """Reconstruct in-memory state from the persisted caches.

    Anything missing reverts to empty — a full sync on the first tick repopulates
    everything. The aggregated-projects cache is rebuilt from raw docs at load time
    so the two views can't diverge across restarts.
    """
    docs_by_pn = load_local_docs()
    notion_entries = load_notion_local()
    discord_by_pn = {ch.projectNumber: ch for ch in load_discord_local()}
    now_ms = int(time.time() * 1000)
    projects = {pn: aggregate_one_project(pn, list(docs.values()), now_ms)
                for pn, docs in docs_by_pn.items()}
    return SyncState(
        docs_by_pn=docs_by_pn,
        projects=projects,
        notion_entries=notion_entries,
        discord_by_pn=discord_by_pn,
        data_source_id=data_source_id,
        category_map={},
    )


def save_state_to_disk(state: SyncState):
    save_local_docs(state.docs_by_pn)
    save_local_projects(list(state.projects.values()))
    save_notion_local_dict(state.notion_entries)
    save_discord_local(list(state.discord_by_pn.values()))


def push_project_to_notion(p: ProjectSummary, state: SyncState) -> bool:
    """Push a project to Notion if it diverges from the cache.

    Returns True when an API write happened. Cache is refreshed from the
    response on every write so it stays a perfect mirror of Notion.
    """
    props = build_notion_props(p)
    entry = state.notion_entries.get(p.projectNumber)
    if entry is not None:
        mdiffs = meaningful_diffs(entry["obj"], p)
        # Set icon only if none is set yet — don't overwrite manually chosen icons.
        desired_icon = page_icon(p) if entry["obj"].icon is None else None
        if not (force_notion_sync or mdiffs or desired_icon):
            return False
        updated = notion_call(notion.pages.update, page_id=entry["page_id"],
                              properties=props,
                              **({"icon": desired_icon} if desired_icon else {}))
        logging.info("Updated  Notion  %s (%s) — %s",
                     p.projectNumber, p.representativeJob,
                     ", ".join(sorted(mdiffs.keys())) or "icon")
        if isinstance(updated, dict):
            extracted = extract_notion_entry(updated)
            if extracted is not None:
                _, new_entry = extracted
                state.notion_entries[p.projectNumber] = new_entry
        return True
    # New page.
    icon = page_icon(p)
    created = notion_call(notion.pages.create,
                          parent={"data_source_id": state.data_source_id},
                          properties=props,
                          **({"icon": icon} if icon else {}))
    logging.info("Created  Notion  %s (%s)", p.projectNumber, p.representativeJob)
    if isinstance(created, dict):
        extracted = extract_notion_entry(created)
        if extracted is not None:
            _, new_entry = extracted
            state.notion_entries[p.projectNumber] = new_entry
    return True


def push_project_to_discord_topic(p: ProjectSummary, state: SyncState) -> tuple[bool, bool]:
    """Push the project's Discord channel topic if EWX or Notion URLs changed.

    Returns `(attempted, skipped_no_channel)`. The cache is updated on success
    so the next tick won't re-diff.
    """
    ch = state.discord_by_pn.get(p.projectNumber)
    if not ch:
        return False, True
    notion_entry = state.notion_entries.get(p.projectNumber)
    notion_url = notion_entry["obj"].notionUrl if notion_entry else None
    ewx_changed    = bool(p.representativeUrl) and ch.eventworxUrl != p.representativeUrl
    notion_changed = bool(notion_url) and ch.notionUrl != notion_url
    if not (ewx_changed or notion_changed):
        return False, False
    new_ewx_url = p.representativeUrl or ch.eventworxUrl
    new_notion_url = notion_url or ch.notionUrl
    new_topic = build_discord_topic(ch.topic, p.projectNumber, new_ewx_url, new_notion_url)
    prefix_ = "[DRY-RUN] " if dry_run else ""
    verb = "Would update" if dry_run else "Updated "
    if not dry_run:
        update_discord_topic(ch.channelId, new_topic)
        ch.topic = new_topic
        ch.eventworxUrl = new_ewx_url
        ch.notionUrl = new_notion_url
    logging.info("%s%s Discord topic %s → EWX=%s Notion=%s",
                 prefix_, verb, p.projectNumber,
                 "set" if ewx_changed else "unchanged",
                 "set" if notion_changed else "unchanged")
    return True, False


def push_projects(pns: list[str], state: SyncState):
    """Push each project to Notion and Discord and log per-subsystem totals."""
    notion_pushed = notion_unchanged = 0
    discord_pushed = discord_unchanged = discord_skipped = 0
    for pn in pns:
        p = state.projects.get(pn)
        if p is None:
            continue
        if push_project_to_notion(p, state):
            notion_pushed += 1
        else:
            notion_unchanged += 1
        pushed, skipped = push_project_to_discord_topic(p, state)
        if skipped:
            discord_skipped += 1
        elif pushed:
            discord_pushed += 1
        else:
            discord_unchanged += 1
    logging.info("Notion: %d pushed, %d unchanged.", notion_pushed, notion_unchanged)
    logging.info("Discord topics: %d %s, %d unchanged, %d skipped (no channel).",
                 discord_pushed,
                 "would-update" if dry_run else "updated",
                 discord_unchanged, discord_skipped)


def apply_changed_docs(state: SyncState, changed: list[Document]) -> set[str]:
    """Merge changed docs into state.docs_by_pn and re-aggregate affected projects.

    Returns the set of affected projectNumbers so the caller can push them.
    A doc whose projectNumber is empty is skipped — Eventworx returns such rows
    for unassigned drafts and they don't belong to any aggregate.
    """
    affected: set[str] = set()
    for d in changed:
        if not d.projectNumber:
            continue
        bucket = state.docs_by_pn.setdefault(d.projectNumber, {})
        bucket[_doc_key(d)] = d
        affected.add(d.projectNumber)
    now_ms = int(time.time() * 1000)
    for pn in affected:
        docs = list(state.docs_by_pn[pn].values())
        state.projects[pn] = aggregate_one_project(pn, docs, now_ms)
    return affected


def apply_changed_notion(state: SyncState, changed: list[tuple[str, dict]]) -> set[str]:
    """Mirror Notion changes into state.notion_entries.

    Returns affected projectNumbers. These need to be re-evaluated against the
    EWX-derived desired state — a human edit to Notion that diverges from EWX
    will be reverted on the next push (EWX is authoritative).
    """
    affected: set[str] = set()
    for pn, entry in changed:
        state.notion_entries[pn] = entry
        affected.add(pn)
    return affected


def full_sync(session, token, state: SyncState) -> int:
    """Replace all caches from authoritative sources and push every project.

    Returns the tick_ms-style timestamp the caller should use as the new EWX
    checkpoint. The Notion checkpoint is taken inside this function — see
    sync_state.json: `last_notion_check_iso` is set by the caller to whatever
    this returns (converted), so self-edits made *during* the push pass surface
    on the next incremental tick and refresh the cache (perfect-mirror invariant).
    """
    logging.info("Full sync starting.")

    state.category_map = fetch_job_categories(session, token)
    all_docs = fetch_all_docs(session, token, state.category_map)
    state.docs_by_pn = {}
    now_ms = int(time.time() * 1000)
    for pn, docs in group_docs_by_project(all_docs).items():
        state.docs_by_pn[pn] = {_doc_key(d): d for d in docs}
    state.projects = {pn: aggregate_one_project(pn, list(d.values()), now_ms)
                      for pn, d in state.docs_by_pn.items()}
    logging.info("Aggregated %d projects.", len(state.projects))

    state.notion_entries = fetch_existing_notion_entries(state.data_source_id)
    logging.info("Fetched %d Notion entries.", len(state.notion_entries))

    state.discord_by_pn = {ch.projectNumber: ch for ch in fetch_discord_channels()}

    # Create job channels for active non-Technikmiete projects that lack one.
    # Runs before the push loop so the new channels join state.discord_by_pn
    # in time for the topic-update pass.
    sync_job_channels(list(state.projects.values()), state.discord_by_pn)

    push_projects(sorted(state.projects.keys()), state)

    sync_vermietungen_threads(list(state.projects.values()))

    save_state_to_disk(state)
    return now_ms


def incremental_sync(session, token, state: SyncState,
                     last_ewx_check_ms: int,
                     last_notion_check_iso: str | None) -> tuple[int, str | None]:
    """Fetch only what changed since the last checkpoints; push affected projects.

    Returns the new (ewx_checkpoint_ms, notion_checkpoint_iso) to persist. The
    checkpoints are captured at the *start* of the tick (before any push) so
    self-edits during the tick will be re-pulled on the next probe and the
    Notion cache mirror stays correct.
    """
    tick_ms = int(time.time() * 1000)
    # Notion checkpoint = current time in ISO; advance even when no probe runs.
    tick_iso = datetime.fromtimestamp(tick_ms / 1000.0, tz=timezone.utc).isoformat()

    changed_docs = fetch_changed_docs(session, token, last_ewx_check_ms, state.category_map)
    affected = apply_changed_docs(state, changed_docs)
    logging.info("EWX probe: %d changed doc(s) → %d affected project(s).",
                 len(changed_docs), len(affected))

    if last_notion_check_iso is not None:
        changed_pages = fetch_changed_notion_pages(state.data_source_id, last_notion_check_iso)
        notion_affected = apply_changed_notion(state, changed_pages)
        logging.info("Notion probe: %d changed page(s) → %d affected project(s).",
                     len(changed_pages), len(notion_affected))
        affected |= notion_affected

    if not affected:
        return tick_ms, tick_iso

    push_projects(sorted(affected), state)

    # Job channels & vermietungen threads need the full project list; only run when
    # at least one project changed (cheap relative to a tick that pushed real diffs).
    sync_job_channels(list(state.projects.values()), state.discord_by_pn)
    sync_vermietungen_threads(list(state.projects.values()))

    save_state_to_disk(state)
    return tick_ms, tick_iso


# ---------------- DAEMON LOOP ----------------
_stop = False

def _request_shutdown(signum, _frame):
    global _stop
    if not _stop:
        logging.info("Received signal %s — shutting down after current tick.", signum)
    _stop = True

def _interruptible_sleep(seconds: float):
    """Sleep in short slices so SIGINT/SIGTERM ends the wait promptly."""
    end = time.monotonic() + seconds
    while not _stop:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))

def _fmt_ts(ms: int | None) -> str:
    if not ms:
        return "never"
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")

def main():
    signal.signal(signal.SIGINT,  _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    persisted = load_sync_state()
    last_ewx_check_ms:    int | None = persisted.get("last_ewx_check_ms")
    last_notion_check_iso: str | None = persisted.get("last_notion_check_iso")
    last_full_sync_at:    int | None = persisted.get("last_full_sync_at_ms")

    # Resolve the Notion data source id once at startup — it doesn't change at runtime.
    data_source_id = resolve_notion_data_source_id()
    state = load_sync_state_from_disk(data_source_id)

    logging.info("ewxSync daemon starting. poll=%ds, full_sync_interval=%ds. "
                 "last_full_sync_at=%s, last_ewx_check=%s, last_notion_check=%s. "
                 "Cache: %d project(s), %d Notion entry/ies, %d Discord channel(s).",
                 POLL_INTERVAL_SECONDS, FULL_SYNC_INTERVAL_SECONDS,
                 _fmt_ts(last_full_sync_at), _fmt_ts(last_ewx_check_ms),
                 last_notion_check_iso or "never",
                 len(state.projects), len(state.notion_entries), len(state.discord_by_pn))

    session = auth_token = None
    first_tick = True
    try:
        while not _stop:
            try:
                # Ensure we have a live session for this tick.
                if session is None or auth_token is None:
                    session, auth_token = try_login(USERNAME, PASSWORD)

                tick_ms = int(time.time() * 1000)
                # Force a full sync on the first tick (so restarting the daemon
                # re-applies current sync logic to every project) and at least
                # once per FULL_SYNC_INTERVAL_SECONDS as a drift safety net for
                # anything the incremental probes might miss (hard deletes,
                # backend bulk edits that don't bump lastModification, etc).
                need_full = (first_tick or last_full_sync_at is None or
                             tick_ms - last_full_sync_at >= FULL_SYNC_INTERVAL_SECONDS * 1000
                             or last_ewx_check_ms is None)

                if need_full:
                    if last_full_sync_at is None:
                        reason = "first run"
                    elif first_tick:
                        reason = "startup full sync"
                    else:
                        reason = "hourly full sync"
                    logging.info("Tick: %s.", reason)
                    new_ewx_check = full_sync(session, auth_token, state)
                    last_full_sync_at  = tick_ms
                    last_ewx_check_ms  = new_ewx_check
                    last_notion_check_iso = datetime.fromtimestamp(
                        new_ewx_check / 1000.0, tz=timezone.utc).isoformat()
                else:
                    new_ewx_check, new_notion_iso = incremental_sync(
                        session, auth_token, state,
                        last_ewx_check_ms, last_notion_check_iso)
                    last_ewx_check_ms = new_ewx_check
                    last_notion_check_iso = new_notion_iso

                save_sync_state({
                    "last_full_sync_at_ms":  last_full_sync_at,
                    "last_ewx_check_ms":     last_ewx_check_ms,
                    "last_notion_check_iso": last_notion_check_iso,
                })
                logging.info("Tick OK. last_full_sync_at=%s, last_ewx_check=%s, last_notion_check=%s",
                             _fmt_ts(last_full_sync_at), _fmt_ts(last_ewx_check_ms),
                             last_notion_check_iso)
                # Clear the startup flag only after a successful sync, so a failed
                # first tick still triggers a full sync on the next attempt.
                first_tick = False
            except Exception:
                # Reactive re-login: drop the session, the next tick logs back in.
                # Timestamps are NOT advanced — same window will be retried.
                logging.exception("Tick failed — dropping session and retrying next tick.")
                if session is not None:
                    try:
                        logout(session, auth_token)
                    except Exception:
                        pass
                session = auth_token = None

            _interruptible_sleep(POLL_INTERVAL_SECONDS)
    finally:
        if session is not None and auth_token is not None:
            try:
                logout(session, auth_token)
            except Exception:
                logging.exception("Logout on shutdown failed.")
        logging.info("ewxSync daemon stopped.")

if __name__ == "__main__":
    main()
