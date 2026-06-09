# ewxSync Goals

Tracking the broader integration goals across Eventworx, Notion, and Discord.

## Identity & sync model

- **Project number is the single source of truth** linking Eventworx, Notion, and Discord.
- **Daemon model**: full sync on startup and daily; in between, incremental ticks every 60s probe EWX (`lastModification` filter) and Notion (`last_edited_time` filter) for changes, diff against the in-memory mirror, and only push changes to Notion and Discord (Eventworx is read-only).
- **Discord join key**: every relevant channel carries a `[EWX:P-XXXX]` tag in its topic; the tag is what matches the channel to its Eventworx project. No need to persist Discord channel IDs anywhere — they're rediscovered each sync. Vermietungen threads use a `P-XXXX | ` name prefix instead.
- The daemon is memory-only; the `cache/local_*.json` files are DEBUG-only snapshots, never read back.

## Watching Eventworx

- [ ] **Project-based system (announced by Eventworx, not live yet).** The frontend already ships scaffolding: `EventWorx.model.Project` with a `status` field, REST `/backend/project` (currently `total: 0`; all job `projectId` are null). When it goes live, ewxSync could read project status directly instead of deriving it via `classify_project()`. Detection checklist + full entity description: see "Dormant `Project` entity" in [eventworx API analysis.md](eventworx%20API%20analysis.md); rerun `cache/probe_project.py` after EWX updates.
- [ ] **App-token auth (`appType=restapi`) instead of the READONLY-license login.** Officially documented at https://api-doc.eventworx.biz/#/ (spec snapshot: `cache/eventworx-api.yaml`): send the token in the **`S-API-TOKEN`** HTTP header; token is minted in the program config UI. Would remove license contention, the forceLogoff risk, and the logout requirement. Remaining unknown: whether the paths are the same `/backend/*` ones (likely) — verify with one probe after creating a token. See "Official REST API documentation" in [eventworx API analysis.md](eventworx%20API%20analysis.md).
- [ ] **Push instead of polling — scaffolded, not live.** STOMP client bundled and a `shared-token/websocket` route exists, but nothing instantiates it. Re-check after frontend updates (`Stomp.client(` in a fresh `app.pretty.js`); also `/backend/programconfig/get-effective-features` may expose feature flags.
- [ ] **Signed no-auth ICS calendar feeds** (`/backend/calendar/sign` → `/backend/noauth/calendar/abbo/new/<sig>`): filterable by orders/offers/categories — could feed Discord scheduled events or shared calendars with zero license cost.
- [ ] **Job todos → Discord** (`/backend/jobtodo/list`: title, responsible, dueDate, status per job) — candidate for posting open todos into the project's channel/thread.
- [ ] **Cheap session health check**: `/backend/check/ping` could validate the EWX session proactively instead of letting a tick fail.

## Cross-system linking

- [x] **Retrieve Eventworx job URL** for each job/project (build canonical URL from API fields).
  - URL on `representativeUrl` in `ProjectSummary`, embedded as `text.link` on the Notion `Project Number` field.
- [x] **Populate Discord channel topic EWX tag with Eventworx link.**
  - Tag format: `[EWX:P-XXXX](eventworx-url)`. Updated each sync if the URL is missing or stale.
- [ ] **Retrieve Discord channel URL** for each channel (`https://discord.com/channels/{guild_id}/{channel_id}`).
- [ ] **Store Discord channel URL in Notion** — field TBD (not Project Number, which links to Eventworx).
- [x] **Add Notion page link to Discord channel topic** alongside the EWX tag.
  - `[Notion](url)` tag inserted/updated right after the EWX tag by the topic-update pass.

## Discord channel lifecycle

- [x] **Auto-create a Discord channel** when a new Eventworx project appears (mirrors the Notion auto-create behavior).
  - `sync_job_channels()` for active non-Technikmiete projects; Technikmiete projects get a vermietungen thread instead (`sync_vermietungen_threads()`).
- [x] **Channel naming**: `YYMMDD Title`
  - Date = first day of rent in Eventworx (rent start date).
  - Title = the Eventworx project title.
  - Example: `261205 Sommerfest Müller`.
- [ ] **Sync changes to Discord**: when the rent start date or project title changes in Eventworx, rename the Discord channel accordingly.
  - Done for vermietungen *threads* (renamed in place); job *channels* are create-only and never renamed.
- [ ] **Archive on completion or cancellation**: move channel into an archive category once the Eventworx job status is `Abgeschlossen` or `Storniert`.
  - Done for vermietungen *threads* (archived when the project leaves the target set); job *channels* are still relocated manually.

## Change notifications in Discord

- [ ] **Post a message in the channel when an Eventworx job changes.** (partially done)
  - [x] New documents created (orders, offers, invoices, etc.) — `announce_new_docs()`.
  - [x] Document status changes — `announce_status_changes()` with per-docType friendly labels.
  - [ ] Job name changes.
  - [ ] Job date changes (rent start/end).
  - **Do not notify on price changes** — too noisy.

## Bugfixes

From the 2026-06-09 code review of `ewxSync.py`. Priorities: **P1** = can hang/kill the daemon or silently lose data; **P2** = real bug or robustness gap with limited blast radius; **P3** = polish / tripwires.

### P1 — daemon stability & data loss

- [x] **Add timeouts to every HTTP call.** `TimeoutSession` (a `requests.Session` subclass defaulting to `HTTP_TIMEOUT = (10, 60)`) now backs both the EWX session and a shared `_http` session for all Discord calls.
- [x] **Remove `sys.exit(1)` from `try_login`.** All failure paths now raise (handled by the tick's retry); `LICENSE-NOT-AVAILABLE` triggers one retry with `forceLogoff: "true"` to reclaim a stale session after a hard crash; `r.raise_for_status()` added.
- [x] **Capture the full-sync EWX checkpoint *before* fetching.** `now_ms` is now taken at the top of `full_sync()`, before any fetch — matching `incremental_sync()`.

### P2 — correctness & robustness

- [ ] **Treat `activation == "deleted"` docs as dead.** `classify_project()` and the invoice-price filter only exclude `"archived"`; a deleted order with an active status keeps its project Aktiv forever. Use `activation not in ("archived", "deleted")`. *Confirmed against the app (2026-06-10): every EWX view filters `deleted|* = "false"` server-side — the UI never shows deleted docs, so neither should we.*
- [ ] **Handle Discord 429s / rate limits.** Notion has `notion_call` with backoff; Discord calls are bare `raise_for_status()`. Channel/thread name PATCHes are limited to 2 per 10 min per channel — a batch-rename tick will 429, abort, and retry-loop. Add a `discord_call` wrapper honoring `Retry-After`.
- [ ] **Guard the Notion `Rent` date payload.** When only `rentEndDate` is set, `{"start": None, "end": …}` is sent — Notion requires `start`, the write fails, and the tick crash-loops until the data changes. Promote end→start (or skip) when start is null.
- [ ] **Floor the Notion checkpoint to the minute.** Notion truncates `last_edited_time` to minute precision; a second-precision `on_or_after` checkpoint can miss edits made later in the same minute until the daily full sync. Subtract 60s / truncate — the self-edit-tolerant design absorbs the re-reads.
- [ ] **Make `fetch_changed_docs` pagination shift-proof.** Offset paging over a result set sorted by `lastModification ASC` can skip a row at a page boundary when a doc is modified mid-pagination. Re-query with `since = last row's modificationDate` instead of `start`/`limit`.
- [ ] **Restrict the invoice price override to `finalInvoice`.** (found 2026-06-10) Invoices carry `docSubType` (`finalInvoice` / `invoicecorrection` / `downpayment`), which `_parse_doc_row` doesn't capture. An `applied` correction invoice is eligible for the price override today and would win if modified last (near-miss observed in P-1086); a `downpayment` invoice would set `currentPrice` to a partial amount. Capture `docSubType` on `Document` and filter the override to `docSubType == "finalInvoice"` (treat empty as final for backward compat). See "docSubType" in [eventworx API analysis.md](eventworx%20API%20analysis.md).
- [ ] **Defend the EWX checkpoint against clock skew.** `last_ewx_check_ms` is the local clock but `lastModification` is the server's; a server clock running behind loses changes until full sync. Subtract a small margin (~30s, merges are idempotent) or advance to `max(modificationDate seen)`.

### P3 — polish

- [ ] **Validate required env vars at startup** and fail fast with a list of everything missing (currently: requests to `None/backend/login`, confusing mid-flight Notion auth errors).
- [ ] **Dry-run fidelity for threads.** The DEBUG branches for thread create/unarchive don't populate `final_state`, so `announce_new_docs()` dry-runs report "skipped (no destination)" where a real run would post. Mirror the synthetic `dry-run:` channel trick used for job channels.
- [ ] **Fix `Document.jobCategoryNames` typing.** Annotated `list[str]` with default `None` and no `__post_init__` normalization (unlike `ProjectSummary.categories`); call sites cover it with `or []`, but it's a tripwire.
- [ ] **Decide startup behavior when Notion is down.** `resolve_notion_data_source_id()` runs before the retry loop, so the daemon crashes at boot instead of retrying — matters under systemd/Task Scheduler auto-start after an outage. Either retry it or document fail-fast as intended.
