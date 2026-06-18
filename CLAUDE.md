# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ewxSync** synchronizes Eventworx (event management SaaS) job data into Notion and Discord. The daemon runs purely in memory: each tick fetches fresh data from EWX (and optionally Notion), diffs against the in-memory mirror, and pushes only changes.

- `ewxSync.py` — the active daemon (repo root). All sync logic lives here.
- `goals.md` — roadmap (open/done feature goals) and the **prioritized bugfix backlog**. Check it before assuming a quirk is intended behavior: known open bugs (P2/P3) are listed there, not fixed silently.
- `eventworx API analysis.md` — reverse-engineered notes on EWX request shapes and filter values.
- `helpers/` — standalone, manually-run scripts; none are part of the daemon:
  - `ewxApiTest.py` — probe for hand-testing single Eventworx endpoints. Logs in, fires one configurable request, dumps the response to `cache/ewx_api_test_response.json`, logs out. Useful when reverse-engineering new filter/sort fields.
  - `fetch_locales.py` — pulls the EWX locale table (`<base>/eventworx/resources/locales/<locale>.json`, the dict `EwLocales` loads at runtime) and writes `cache/locale_<locale>.json`. Source of the per-docType `Common.JobStatusMap` status→label tree behind `STATUS_PHRASES`.
  - `beautify_app.py` — reformats the minified `cache/app.js` Eventworx bundle into readable, line-navigable `cache/app.pretty.js` (via `jsbeautifier`).
  - `discord_list_channels.py` / `discord_cache_channels.py` / `discord_apply_tags.py` — one-off Discord channel inspection, caching, and `[EWX:…]` tagging utilities.
  - `reduce_ewx_cache.py` — slims `cache/local_eventworx_projects.json` to the fields needed for AI-assisted Discord channel matching.
  - `notionDiscord.py` — old prototype (Discord bot that mirrored new channels to Notion). No longer used.
- `cache/` — git-ignored scratch space holding every generated JSON and JS file: daemon debug snapshots, raw API dumps, locale pulls, and the `app.js`/`app.pretty.js` bundles. Nothing here is read by the daemon (see [Local snapshots](#local-snapshots-debug-only-git-ignored)).

### Where to look things up (reference files)

| Question | Look in |
|---|---|
| How does an EWX endpoint/filter/status behave? | [eventworx API analysis.md](eventworx%20API%20analysis.md) — endpoints, filter language, Solr↔response field map, status vocabularies, per-docType label tables, verified findings (project entity, REST API, docSubType). |
| Is this quirk a known bug? What's planned? | [goals.md](goals.md) — feature roadmap, prioritized bugfix backlog, "Watching Eventworx" items. |
| What does the EWX frontend *actually* do? | `cache/app.pretty.js` — beautified frontend bundle, the ground truth for reverse-engineering. **~500k lines — never read whole; Grep first, then Read the hit ±50 lines.** Regenerate from a fresh `cache/app.js` via `helpers/beautify_app.py`. Entry points: models `Ext\.cmd\.derive\('EventWorx\.model\.<Name>'` (Job, Project, Notes, JobTodoListEntry…); status stores `lookups\.JobStatus` / `JobStatusMap`; backend route map `EventWorx.controller.EventWorxConfig` (search `backendUrl:`); view filter logic `case_offer` / `case_order` / `selectedPhase`. |
| What label does the UI render for a status? | `cache/locale_de.json` — runtime locale table (`Common.JobStatusMap` = per-docType status→German label). Refetch via `helpers/fetch_locales.py`. |
| What does the official REST API support? | `cache/eventworx-api.yaml` — snapshot of https://api-doc.eventworx.biz/eventworx-api.yaml (Swagger 2.0; auth header `S-API-TOKEN`). Re-download and diff after EWX updates. |
| What does a raw API response look like? | `cache/all_eventworx_raw.json` (full `/backend/job` dump, written by a DEBUG full sync); `cache/ewx_api_test_response.json` (single-endpoint probes via `helpers/ewxApiTest.py`). |
| One-off live probes | `cache/probe_project.py` (+ `probe_project_response.json`) — `/backend/project` probe; pattern to copy for new throwaway probes (login with `forceLogoff=false` fails harmlessly if the daemon holds the license). |

Line numbers in past findings refer to the current `app.pretty.js` and **drift whenever Eventworx ships a new bundle** — always re-grep instead of trusting saved offsets.

## Running Scripts

The repo's `.venv` is a **Windows** venv (`.venv\Scripts\`).

```powershell
# Activate the virtual environment first (PowerShell)
.venv\Scripts\Activate.ps1

# Main sync daemon (long-running)
python ewxSync.py

# Dry-run mode: log all writes as [DRY-RUN] and dump in-memory state to cache/*.json
$env:DEBUG = "true"; python ewxSync.py
```

On Linux (e.g. production host): `source .venv/bin/activate` and `DEBUG=true python ewxSync.py`.

Stop with Ctrl+C / SIGTERM — the daemon logs out cleanly via a signal handler.

No build step, test runner, or linter is configured.

## Configuration

### Environment variables (read via `os.getenv` at the top of `ewxSync.py`)

| Variable | Purpose |
|---|---|
| `NOTION_TOKEN` | Notion integration token (internal integration). Required. |
| `DATABASE_ID` | Notion database ID for the projects database. Required. |
| `EVENTWORX_BASE` | Eventworx instance base URL, e.g. `https://acme.eventworx.eu`. Required. |
| `EVENTWORX_USERNAME` | EWX login. Required. |
| `EVENTWORX_PASSWORD` | EWX password. Required. |
| `DISCORD_TOKEN` | Discord bot token. Required. |
| `DISCORD_GUILD_ID` | Discord server (guild) ID. Required. |
| `DISCORD_VERMIETUNGEN_CHANNEL_ID` | Channel that holds one thread per Technikmiete project. Required for `sync_vermietungen_threads`. |
| `DISCORD_JOBS_CATEGORY_ID` | Category under which non-Technikmiete project channels are created. Required for `sync_job_channels`. |
| `DISCORD_CREW_ROLE_ID` | Role to mention when a vermietungen thread is created, so its members auto-subscribe to the thread. Optional — when unset, the crew ping is skipped. |
| `ANNOUNCE_NEW_DOCS` | `true`/`1`/`yes`/`on` (default `true`) enables the Discord "`<Typ> <Nummer>` wurde erstellt" messages. Set to `false`/`0`/`no`/`off` to kill the feature: no per-document messages are posted and new threads fall back to a plain crew ping. Optional. |
| `ANNOUNCE_STATUS_CHANGES` | `true`/`1`/`yes`/`on` (default `true`) enables the Discord "`<Typ> <Nummer> <Status-Phrase>`" messages (e.g. "ist bestätigt"). Set to `false`/`0`/`no`/`off` to kill the feature. Independent of `ANNOUNCE_NEW_DOCS`. Optional. |
| `DEBUG` | `true`/`1`/`yes`/`on` puts the daemon in dry-run / observation mode: every external write (Notion page create/update, Discord channel create, channel topic update, thread create/rename/archive/unarchive, crew ping) is logged with a `[DRY-RUN]` prefix instead of called, and a snapshot of in-memory state is written to disk after every tick. Off by default. The daemon never reads from disk in any mode. |

## Architecture

### Key entry points

| Symbol | Role |
|---|---|
| `main()` | Daemon loop. Decides full vs incremental, advances checkpoints. |
| `full_sync()` | Replace every in-memory cache from API. Runs on first tick of every daemon and at least once per `FULL_SYNC_INTERVAL_SECONDS`. |
| `incremental_sync()` | Probe EWX + Notion for changes since last checkpoint, mutate state in place, push affected projects. |
| `aggregate_one_project()` → `classify_project()` | Turn a project's docs into a `ProjectSummary` with status, representative doc, and currentPrice. |
| `push_projects()` | Fan-out: `push_project_to_notion()` + `push_project_to_discord_topic()` per affected project. |
| `sync_job_channels()` | Create a Discord channel for each active non-Technikmiete project that lacks one. |
| `sync_vermietungen_threads()` | Maintain one thread per active Technikmiete project; backfill the crew ping into threads that don't have one yet. |
| `meaningful_diffs()` | Notion-write gate: only diff on fields whose new value is non-empty, so empty EWX data never clears Notion. |
| `ping_crew_in_thread()` / `thread_has_crew_ping()` | Send the role mention that auto-subscribes Crew members (optionally prefixed with announcement lines); detect whether a thread already received one. |
| `announce_new_docs()` | Post a "`<Typ> <Nummer>` wurde erstellt" message into a project's channel/thread for each newly created EWX document (incremental ticks only). |
| `announce_status_changes()` | Post a "`<Typ> <Nummer> <Status-Phrase>`" message (e.g. "ist bestätigt") for each previously-seen document whose `status` field changed (incremental ticks only). |

### Sync model

- Documents from EWX are grouped by `projectNumber` into `ProjectSummary` objects.
- `classify_project()` picks the representative doc using the same logic Eventworx's UI uses for its "active orders/offers" views: status sets per docType, `activation != "archived"`, `dealType in {rent, sale}`, and `endDate > now` for offers. (Eventworx's own "active" views only show `rent`; we intentionally also track `sale` — service-only jobs with no rented equipment — so they appear as Aktiv in Notion/Discord. See `ACTIVE_DEAL_TYPES`.)
- Project status has three values:
  - `Aktiv` — a live order or live offer exists
  - `Abgeschlossen` — closed
  - `Storniert` — cancelled
- Categories are merged across all docs in the project (union, sorted).
- Notion sync uses the newer `notion.data_sources` API (notion-client v3).
- Discord sync updates channel topics with Eventworx + Notion URLs.

Active status sets (derived from Eventworx's own filter requests, see [eventworx API analysis.md](eventworx%20API%20analysis.md)):
- **Orders**: `{draft, sent, open}`
- **Offers**: `{draft, sent, open, accepted}` — plus `endDate > now` and no live order in the project

**Offer variant deduplication**: multiple offer variants share the same `jobNumber` (e.g. AN-1073-01, AN-1073-02) and both appear as separate rows in the API response, both with `activation=null`. Before evaluating live offers, `classify_project()` deduplicates by `jobNumber`, keeping only the most recently modified variant. This prevents a rejected variant 2 from being hidden while the older variant 1 (still `sent`) incorrectly marks the project as Aktiv.

**Invoice price override**: when any non-cancelled, non-archived invoice exists for a project, its `overallPriceValue` is used for `currentPrice` instead of the representative doc's price. The rep doc identity (URL, job number, type) is unchanged. Invoices reflect the actually-billed amount, including services added after the order was placed (e.g. P-1197 had order €876.40 but invoice €1376.40). The most recently modified eligible invoice wins.

### Daemon model

**TL;DR**: `main()` is a `while not _stop` loop. First tick of every daemon process = full sync. Every 60s after = incremental sync. At least once per day = full sync. Memory only — restarts always start clean.

One Eventworx login at startup is reused across ticks; the session is only re-established after a tick fails. The daemon holds a `SyncState` dataclass in memory that is the working ground truth across ticks.

**Memory-only operation.** The daemon never reads anything from disk. On startup `new_empty_state(data_source_id)` returns a fresh empty `SyncState` and all checkpoints initialize to `None`, so the first tick is always a full sync. When `DEBUG=true`, every save function additionally writes a snapshot of in-memory state to disk for inspection — those files are still never consumed.

**Full sync** — runs on the first tick (checkpoints start `None`) and at least once every `FULL_SYNC_INTERVAL_SECONDS` (default 86400 = daily). Fetches everything fresh (all EWX docs, all Notion entries, all Discord channels), replaces in-memory state, and pushes every project. Safety net for drift, hard deletes, and missed probes. `state.thread_crew_pinged` is *not* reset — confirmations accumulated during the daemon's lifetime carry over so the per-tick crew-ping check stays at zero once every thread is known.

**Incremental sync** — every tick that isn't a full sync. Two probes, run sequentially:
1. **EWX probe** — `fetch_changed_docs(session, token, last_ewx_check_ms)` paginates `/backend/job` with `filter=lastModification|* > last_ewx_check_ms`. Each returned doc replaces its entry in `state.docs_by_pn[pn][doc_key]` (or is inserted if new). The set of affected `projectNumber`s drives re-aggregation via `aggregate_one_project()`.
2. **Notion probe** — `fetch_changed_notion_pages(data_source_id, last_notion_check_iso)` uses Notion's `last_edited_time` `on_or_after` filter. Returned pages overwrite `state.notion_entries[pn]` verbatim — the in-memory mirror always matches Notion. Self-edits re-appear here on the next tick; that's expected (it keeps the mirror correct) and they yield no diff against the EWX-derived desired state, so no push loop occurs.

After probes, the union of affected projects is pushed via `push_projects()`. Each project: `push_project_to_notion()` diffs against the in-memory mirror via `meaningful_diffs` and writes on divergence (mirror then refreshed from the API response so it stays perfectly aligned with Notion); `push_project_to_discord_topic()` diffs the topic and PATCHes the channel if EWX or Notion URLs changed. `sync_job_channels()` and `sync_vermietungen_threads()` run only when at least one project was affected. Finally `announce_new_docs()` posts a "wurde erstellt" message for each newly created document (see [Document-created announcements](#document-created-announcements)) and `announce_status_changes()` posts a status-phrase message (e.g. "ist bestätigt") for each document whose status changed (see [Document status-change announcements](#document-status-change-announcements)).

Each tick:
1. **Capture timestamps before any fetch.** `tick_ms = int(time.time() * 1000)` and `tick_iso = datetime.fromtimestamp(tick_ms / 1000.0, tz=timezone.utc).isoformat()` are the candidate checkpoints. Captured first so anything modified during the tick is re-probed on the next pass. (`full_sync()` likewise captures its own `now_ms` at the top, before `fetch_all_docs` — re-processing the overlap is idempotent.)
2. **Run full_sync or incremental_sync.**
3. **Advance checkpoints only on full success.** `last_ewx_check_ms`, `last_notion_check_iso`, and (on full ticks) `last_full_sync_at_ms` live in `main()`'s local scope. Any exception aborts before this step, so the same window is retried next tick. When `DEBUG=true`, the checkpoints are also written to `cache/sync_state.json`.
4. **Re-login is reactive.** If anything in the tick raises, the session is dropped (best-effort logout) and `try_login()` runs at the top of the next tick. No proactive token refresh.

Shutdown: `SIGINT`/`SIGTERM` set `_stop = True`. `_interruptible_sleep()` polls the flag at 1s granularity so Ctrl+C exits promptly. The `finally` clause in `main()` logs out before returning.

`POLL_INTERVAL_SECONDS`, `FULL_SYNC_INTERVAL_SECONDS`, and `SYNC_STATE_FILE` are top-of-file constants.

### Data flow

```
[Daemon startup]
  resolve_notion_data_source_id() → new_empty_state(dsid)
  → all SyncState fields start empty; all checkpoints start None.

[Each tick] login (lazy) → capture tick_ms, tick_iso

  IF first_tick OR last_full_sync_at expired OR no EWX checkpoint:
    full_sync(session, token, state):
      fetch_all_docs        → replace state.docs_by_pn, rebuild state.projects
      fetch_existing_notion → replace state.notion_entries
      fetch_discord_channels → replace state.discord_by_pn
      sync_job_channels      (over the full project list)
      push_projects(all PNs) → per-project Notion + Discord topic
      sync_vermietungen_threads (over the full project list)
      save_state_to_disk     (no-op unless DEBUG=true)

  ELSE:
    incremental_sync(session, token, state, last_ewx_check, last_notion_check):
      fetch_changed_docs        → apply_changed_docs   → affected EWX PNs + new docs + status changes
      fetch_changed_notion_pages → apply_changed_notion → affected Notion PNs
      IF affected: push_projects(affected) → per-project Notion + Discord topic
                   sync_job_channels (full list)
                   sync_vermietungen_threads (full list) → folds new-doc lines into new threads
                   announce_new_docs(new docs) → "wurde erstellt" per new doc
                   announce_status_changes(status changes) → status phrase per change
                   save_state_to_disk    (no-op unless DEBUG=true)

  advance last_ewx_check_ms = tick_ms, last_notion_check_iso = tick_iso
  on full sync also: last_full_sync_at_ms = tick_ms
  → save cache/sync_state.json    (no-op unless DEBUG=true)
```

### Data structures

`SyncState` (in-memory ground truth, never persisted in normal mode):

| Field | Type | Role |
|---|---|---|
| `docs_by_pn` | `dict[pn, dict[doc_key, Document]]` | Raw EWX docs grouped by project, keyed by `_doc_key(doc)` = `f"{docType}:{docId}"`. Authoritative input to aggregation. |
| `projects` | `dict[pn, ProjectSummary]` | Derived view of `docs_by_pn`, rebuilt on full sync and patched on incremental ticks. |
| `notion_entries` | `dict[pn, {page_id, last_edited_time, obj}]` | Perfect mirror of Notion. `obj` is the `ProjectSummary` extracted from the page. Refreshed from API responses on every write. |
| `discord_by_pn` | `dict[pn, DiscordChannel]` | Mirror of Discord channels carrying an `[EWX:P-XXXX]` tag. |
| `discord_threads_by_pn` | `dict[pn, DiscordThread]` | Vermietungen threads keyed by project number, refreshed each time `sync_vermietungen_threads()` runs. Drives message routing for `announce_new_docs()`. |
| `data_source_id` | `str` | Cached Notion data source id, resolved once at startup. |
| `category_map` | `dict[category_id, name]` | EWX category id → name. Refreshed on full sync. |
| `thread_crew_pinged` | `dict[threadId, bool]` | True once a Crew role mention is confirmed/sent in the thread. Persists across ticks but not restarts. |

`Document` (one row from EWX `/backend/job`):

| Field | Type | Notes |
|---|---|---|
| `jobNumber` | `str` | EWX job number, e.g. `AN-1073-01`. |
| `projectNumber` | `str` | EWX project number, e.g. `P-1234`. Join key. |
| `docType` | `str` | `order`, `offer`, `request`, `deliverynote`, `invoice` (rarely `clearance`, `repair`). |
| `dealType` | `str \| None` | `rent` or `sale`. Both count as Aktiv (`ACTIVE_DEAL_TYPES`); `sale` = service-only jobs without rented equipment. |
| `title` | `str` | |
| `status` | `str` | Doc-level status, see "Active status sets". |
| `activation` | `str \| None` | `None` (live) \| `"archived"` \| `"active"` \| `"deleted"`. |
| `modificationDate` | `int \| None` | ms timestamp; used for tie-breaking. |
| `overallPriceValue` | `float \| None` | In cents — divide by 100. |
| `endDate` | `int \| None` | ms timestamp; used to check if an offer period has passed. |
| `rentStartDate` / `rentEndDate` | `str \| None` | ISO date strings, minute-truncated. |
| `docId` | `str` | EWX UUID. Used to build deep-link URLs. |
| `jobCategoryNames` | `list[str]` | Resolved from `category_map`. |

`ProjectSummary` (aggregated per project, written to Notion and used for Discord topics):

| Field | Type | Notes |
|---|---|---|
| `projectNumber`, `title`, `status` | `str` | |
| `currentPrice` | `float \| None` | Invoice override applies — see "Invoice price override". |
| `rentStartDate` / `rentEndDate` | `str \| None` | ISO dates from the representative doc. |
| `representativeJob` | `str` | E.g. `AN-1073-01`. |
| `representativeDocType` | `str` | `order` / `offer` / etc. |
| `categories` | `list[str]` | Union across all docs in the project. |
| `has_order` / `has_offer` / `has_request` / `has_delivery` / `has_invoice` | `bool` | Presence of **any** doc of that type in the project — no status/activation filtering. (`has_delivery` maps docType `deliverynote`.) |
| `icon` | `str \| None` | URL or emoji string. Read from Notion; only set on Notion when not already set. |
| `representativeUrl` | `str \| None` | EWX deep-link to the representative doc. Embedded as `text.link` on the Notion `Project Number` field and written into the Discord channel topic EWX tag. |
| `notionUrl` | `str \| None` | URL of the Notion page. Read from Notion and stored in `state.notion_entries`; never written back to Notion, never diffed. Used to populate the Discord topic Notion tag. |

`DiscordChannel`: `channelId`, `channelName`, `projectNumber`, `eventworxUrl` (from topic), `notionUrl` (from topic), `topic` (full raw string).

`DiscordThread`: `threadId`, `threadName`, `projectNumber`, `archived`, `crewPinged` (mirror of `state.thread_crew_pinged`, populated each tick from that dict).

### Format conventions

- Eventworx prices are in **cents** — divide by 100 to get euros.
- Eventworx timestamps are in **milliseconds** — divide by 1000 for Unix seconds.
- Date/time normalizers truncate to the minute to avoid jitter-based spurious diffs.

### Notion database fields

`Title`, `Project Number` (rich_text, hyperlinked to Eventworx representative doc), `Status` (select), `Representative Job` (rich_text), `Representative Type` (select), `Current Price` (number), `Rent` (date range), `Categories` (multi_select), `Has Order/Offer/Request/Delivery/Invoice` (checkboxes).

### Discord channel identification

Relevant channels carry an `[EWX:P-XXXX]` tag in their topic — this is the join key between Discord and Eventworx. The sync script finds these channels via `_EWX_TAG_RE`, extracts the project number and any existing URL, then updates the tag to `[EWX:P-XXXX](eventworx-url)` when the URL is missing or stale. The Notion URL is added/updated as a separate `[Notion](url)` tag right after the EWX tag (`_NOTION_TAG_RE`). Discord supports `[text](url)` markdown in channel topics.

### Job channel auto-create (non-Technikmiete jobs)

`sync_job_channels()` creates a Discord text channel for each active non-Technikmiete project that lacks one, under the category in `DISCORD_JOBS_CATEGORY_ID`.

- **Target set**: projects with `status == "Aktiv"` AND `"Technikmiete" NOT in categories`.
- **Channel naming**: `YYMMDD title` from the project's `rentStartDate` (e.g. `261205 Sommerfest Müller`), truncated to 100 chars (`build_channel_name`). When `rentStartDate` is missing, the date prefix is omitted. Discord normalizes the name (lowercase, spaces → `-`) on creation.
- **Topic on creation**: pre-populated with `[EWX:P-XXXX](url)` so the per-project topic-update pass is a no-op for the new channel.
- **Lifecycle**: create-only. No archive, move, or delete when a project leaves the target set — channel relocation on completion is a manual step (see `goals.md`).
- Newly created channels are inserted into `state.discord_by_pn` so the subsequent per-project loop sees them. Under `DEBUG=true` a *synthetic* channel (fake `dry-run:P-XXXX` id) is inserted instead, so the dry-run's topic-update and `announce_new_docs()` passes route identically to a real run. The fake id is never used for an API call — all writes are gated behind `DEBUG`.

### Vermietungen threads (Technikmiete jobs)

`sync_vermietungen_threads()` manages one thread per active `Technikmiete` project inside the channel specified by `DISCORD_VERMIETUNGEN_CHANNEL_ID`.

- **Target set**: projects with `status == "Aktiv"` AND `"Technikmiete" in categories`.
- **Thread naming**: `P-XXXX | DD.MM. title` (e.g. `P-1234 | 05.12. Sommerfest Müller`), truncated to 100 chars (`build_thread_name`). The `DD.MM.` part comes from the project's `rentStartDate` and is omitted when unset. The `P-XXXX` prefix is the join key — parsed via `_THREAD_PREFIX_RE`, which also accepts the legacy `P-XXXX_title` format so pre-existing threads get renamed in place instead of duplicated. Threads in this channel without either prefix shape are ignored.
- **Reconciliation**:
  - Target project with no active thread → unarchive a matching archived thread if one exists, otherwise create a new public thread (`type=11`, no starter message, 7-day auto-archive).
  - Active thread whose project left the target set → deliver that project's pending announcements into the still-active thread first (any new-doc / status-change lines, e.g. "ist abgeschlossen"), then post a silent `THREAD_ARCHIVE_NOTICE` ("…dieser Thread wird archiviert."), then PATCH `archived: true` (never deleted). The pre-archive announce is necessary because `discord_destination()` skips archived threads, so a status change posted *after* archiving would be dropped. `sync_vermietungen_threads()` therefore takes `status_changes_by_pn` alongside `new_docs_by_pn` and returns `(new_announced, status_announced)` so the caller's `announce_new_docs()` / `announce_status_changes()` passes skip the projects it already handled here.
  - Active thread still in target set whose name diverges from `build_thread_name(...)` → rename in place (e.g. when the Eventworx title changes).
- Archived threads are only listed on demand (when at least one target project lacks an active thread), to avoid the extra paginated API call when not needed.

**Crew ping** (`ping_crew_in_thread`, `thread_has_crew_ping`): on thread creation, the bot posts a `<@&DISCORD_CREW_ROLE_ID>` mention with `[Eventworx](<url>) · [Notion](<url>)` links. Mentioning the role auto-subscribes its members to the thread — that's the entire mechanism. The Notion URL is available because `push_projects()` runs before `sync_vermietungen_threads()` and refreshes `state.notion_entries[pn]["obj"].notionUrl` from the create response. When the thread is created in the same tick that introduced new EWX documents, those documents' "wurde erstellt" lines are folded into this single intro message (above the mention) via the `header_lines` argument, so the announcement is genuinely the thread's first message. The message posts even when `DISCORD_CREW_ROLE_ID` is unset, as long as there are announcement lines to deliver.

Existing threads without a crew ping are backfilled in a final pass over the target set. To avoid re-checking every thread on every tick, the result is recorded in `state.thread_crew_pinged: dict[threadId, bool]` — once `True`, the API check is skipped permanently within the daemon's lifetime. The flag is in-memory only; a daemon restart re-verifies via Discord's message history (one `GET /messages?after=0&limit=50` per thread), then settles back to zero API checks.

### Document-created announcements

`announce_new_docs()` posts a one-line message into a project's Discord destination whenever a **new** EWX document appears: `Auftrag [AU-1234](<url>) wurde erstellt` (every docType — order, offer, request, deliverynote, invoice; German nouns from `EWX_DOC_TYPE_LABELS`). The job number links to the Eventworx deep-link (`eventworx_doc_url`), angle-bracketed to suppress Discord's embed.

- **What counts as "new"**: `apply_changed_docs()` returns the docs whose `_doc_key` (`docType:docId`) was absent from `state.docs_by_pn` before this tick merged it. Because `fetch_changed_docs` returns both created *and* modified docs, this key-presence check is what distinguishes a creation from an edit. Modifications announce nothing.
- **Incremental only**: full sync rebuilds `docs_by_pn` from scratch and never calls `apply_changed_docs`/`announce_new_docs`, so a daemon restart (which full-syncs) never replays the whole history into Discord.
- **Routing** (`discord_destination`): Technikmiete → the active vermietungen thread (`state.discord_threads_by_pn`); otherwise → the job channel (`state.discord_by_pn`). Runs *after* `sync_job_channels()` and `sync_vermietungen_threads()` so a brand-new project's channel/thread already exists. A doc whose project has no destination (e.g. never became Aktiv) is silently skipped.
- **No double-post on new threads**: `sync_vermietungen_threads()` returns the set of project numbers whose new docs it already folded into a freshly created thread's intro message; `announce_new_docs()` skips those.
- **At-most-once**: the new-doc list is derived in memory and never persisted. If a tick fails after the doc is merged but before the announce pass, the doc is already in the mirror next tick and won't re-announce. Acceptable for notifications.
- **DEBUG**: each would-be message logs `[DRY-RUN] Would announce …`; summary line `Announcements: N sent, M skipped (no destination).` (verb becomes `would-send`).
- **Kill-switch**: `ANNOUNCE_NEW_DOCS=false` disables the whole feature — `incremental_sync()` clears the new-doc list, so nothing is posted and new threads fall back to a plain crew ping.

### Document status-change announcements

`announce_status_changes()` posts a one-line message into a project's Discord destination whenever an **existing** EWX document's `status` field changes: `Auftrag [AU-1234](<url>) ist abgeschlossen`. The raw Eventworx status code is translated to a **per-docType** German predicate phrase (verb included) via `status_phrase()` / `STATUS_PHRASES` — necessary because the raw codes are terse and the same code renders differently per docType (an `order`'s `sent` is "Bestätigt", an `offer`'s `sent` is "Gesendet"). The phrases stick to the authoritative German per-docType labels from Eventworx's runtime locale table (`Common.JobStatusMap` in `de.json`, pulled via `helpers/fetch_locales.py`), only bent into a sentence ("Packen" → "wird gepackt"). See [eventworx API analysis.md](eventworx%20API%20analysis.md) for the full tree and the per-docType gotchas. The API exposes no display label, so the mapping lives in code; any docType/status not in the table falls back to `hat jetzt den Status "<code>"`. Same link/routing/skip semantics as document-created announcements. Differences from that feature:

- **What counts as a "status change"**: `apply_changed_docs()` compares each changed doc's `status` against the mirrored value *before* overwriting it. A doc is in `status_changes` when its `_doc_key` already existed and the new `status` is non-empty and differs. Creations are never status changes (a doc is either new or a possible status change, never both). Sub-status fields (`deliveryStatus`, `invoiceStatus`) are **not** tracked — only the main `status`.
- **All transitions announced**: every status change for every docType is posted (no curated subset). Observed `status` values per docType are catalogued in [eventworx API analysis.md](eventworx%20API%20analysis.md), and each is mapped to a German predicate phrase in `STATUS_PHRASES`.
- **Not folded into thread intros, but folded into thread archival**: unlike creations, status changes are never merged into a freshly *created* thread's intro message — they post as their own message. They *are*, however, posted by `sync_vermietungen_threads()` right before it *archives* a thread whose project left the target set (the completed order's "ist abgeschlossen" should land in the thread before it goes away). Those projects come back in the `status_announced` skip set, so `announce_status_changes()` takes an `announced_pns` argument to avoid a double-post.
- **Incremental only / at-most-once / DEBUG**: identical to document-created announcements. Summary line: `Status announcements: N sent, M skipped (no destination).`
- **Kill-switch**: `ANNOUNCE_STATUS_CHANGES=false` disables the feature independently of `ANNOUNCE_NEW_DOCS`.

### Logging convention

One log line per *change*, one summary line for the *no-ops*. Each subsystem prints a summary at the end of its pass:
- Notion: `Notion: N pushed, M unchanged.` (the verb becomes `would-push` under `DEBUG=true`.)
- Discord topics: `Discord topics: N updated, M unchanged, K skipped (no channel).` (the verb becomes `would-update` under `DEBUG=true`.)
- `sync_job_channels()` and `sync_vermietungen_threads()` print plan/result summaries before and after their action loops.

Under `DEBUG=true` every would-be write (Notion + Discord) is prefixed with `[DRY-RUN]` so the log reads identically to a real run minus the API calls.

### HTTP conventions & Eventworx auth

**Timeouts**: every EWX and Discord call goes through `TimeoutSession`, a `requests.Session` subclass that defaults `timeout` to `HTTP_TIMEOUT = (10, 60)` (connect, read) — `try_login()` returns one for EWX, and the module-level `_http` session handles all Discord calls. Never add a bare `requests.get/post/patch` call; use the session so a stalled connection raises instead of hanging the daemon forever. (Notion calls go through notion-client, which has its own 60s default.)

**Login** uses `license: READONLY` to avoid consuming a full user license. The `X-AUTH-TOKEN` header from the login response must be included in all subsequent requests. `try_login()` **raises** on every failure path (never exits the process) so the daemon's tick handler retries next tick. On `LICENSE-NOT-AVAILABLE` it retries once with `forceLogoff: "true"` to reclaim a stale session (e.g. our own, after a hard crash that skipped logout). **Caution**: this means a second concurrently running instance — including a local test login — will kick the production daemon off the license. Always call `logout()` — a `try/finally` block ensures this even on error.

### Local snapshots (debug-only, git-ignored)

None of these files are read by the daemon. They live under `cache/` (git-ignored) and are written only when `DEBUG=true` is set in the environment, as point-in-time snapshots of in-memory state for inspection and debugging. Deleting them has no effect on behavior.

| File | What it contains when `DEBUG=true` |
|---|---|
| `cache/local_eventworx_docs.json` | Raw EWX `Document` records grouped by `projectNumber` and keyed by `docType:docId`. |
| `cache/local_eventworx_projects.json` | Aggregated `ProjectSummary` snapshot — derived view of `cache/local_eventworx_docs.json`. |
| `cache/local_notion_projects.json` | Mirror of Notion at the end of the last tick. Each entry stores `page_id`, `last_edited_time`, and the `ProjectSummary` extracted from the page. |
| `cache/local_discord_channels.json` | Snapshot of Discord channels carrying an `[EWX:P-XXXX]` tag. |
| `cache/local_discord_threads.json` | Snapshot of vermietungen threads including the in-memory `crewPinged` flag for each. |
| `cache/all_eventworx_raw.json` | Raw API response dump from the most recent full sync. |
| `cache/sync_state.json` | Snapshot of `last_full_sync_at_ms`, `last_ewx_check_ms`, `last_notion_check_iso`. |
| `cache/ewx_api_test_response.json` | Output of `helpers/ewxApiTest.py` probes; unrelated to the daemon. |

## Dependencies

```bash
# Daemon (ewxSync.py) — talks to Discord via raw REST, no discord.py needed
pip install requests notion-client python-dotenv

# Helpers only: discord.py for the discord_* scripts, jsbeautifier for beautify_app.py
pip install discord.py jsbeautifier
```

Python 3.10+ required (uses `int | None` union syntax in ewxSync.py). A `.env` file at the repo root supplies the environment variables listed under [Configuration](#configuration); `python-dotenv` loads it at startup.
