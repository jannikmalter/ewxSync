# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ewxSync** synchronizes Eventworx (event management SaaS) job data into Notion and Discord. Each sync pass fetches fresh data from all three services, diffs locally, and pushes only changes.

`notionDiscord.py` is an old prototype (Discord bot that mirrored new channels to Notion) and is no longer the active approach. All sync logic lives in `ewxSync.py`.

`ewxApiTest.py` is a standalone probe for hand-testing single Eventworx endpoints — logs in, fires one configurable request, dumps the response to `ewx_api_test_response.json`, logs out. Useful when reverse-engineering new filter/sort fields.

## Running Scripts

```bash
# Activate the virtual environment first
source .venv/bin/activate

# Main sync daemon (long-running, see "Daemon model" below)
python ewxSync.py
```

The script is a long-running daemon, not a cron job. Stop it with Ctrl+C / SIGTERM — it logs out cleanly via a signal handler.

No build step, test runner, or linter is configured.

## Architecture

### Sync model

`ewxSync.py`:
- Groups all Eventworx documents by `projectNumber` into `ProjectSummary` objects
- `classify_project()` determines status and picks the representative doc using the same logic Eventworx's UI uses for its "active orders/offers" views — status sets per docType, `activation != archived`, `dealType = rent`, and `endDate > now` for offers
- Status → 3 values: `Aktiv` (live order or offer exists), `Abgeschlossen` (closed), `Storniert` (cancelled)
- Categories are merged across all docs in the project (union, sorted)
- Syncs to Notion using the newer `notion.data_sources` API (notion-client v3)
- Syncs to Discord by updating channel topics with Eventworx URLs
- `meaningful_diffs()` prevents overwriting existing Notion values with empty/null data from Eventworx

Active status sets (derived from Eventworx's own filter requests, see `eventworx API  analysis.md`):
- Orders: `{draft, sent, open}`
- Offers: `{draft, sent, open, accepted}` — plus `endDate > now` and no live order in the project

**Offer variant deduplication**: multiple offer variants share the same `jobNumber` (e.g. AN-1073-01, AN-1073-02) and both appear as separate rows in the API response, both with `activation=null`. Before evaluating live offers, `classify_project()` deduplicates by `jobNumber`, keeping only the most recently modified variant. This prevents a rejected variant 2 from being hidden while the older variant 1 (still `sent`) incorrectly marks the project as Aktiv.

**Invoice price override**: when any non-cancelled, non-archived invoice exists for a project, its `overallPriceValue` is used for `currentPrice` instead of the representative doc's price. The rep doc identity (URL, job number, type) is unchanged. Invoices reflect the actually-billed amount, including services added after the order was placed (e.g. P-1197 had order €876.40 but invoice €1376.40). The most recently modified eligible invoice wins.

### Daemon model

`main()` is a `while not _stop` loop. One Eventworx login at startup is reused across ticks; the session is only re-established after a tick fails. The daemon holds a `SyncState` dataclass in memory (raw EWX docs by project, aggregated `ProjectSummary`, Notion entries, Discord channels) that is the working ground truth across ticks. On startup the state is hydrated from the on-disk caches and the first tick is always a full sync.

Two tick modes:

**Full sync** — runs on the first tick, when `now - last_full_sync_at >= FULL_SYNC_INTERVAL_SECONDS` (default 3600), or when no EWX checkpoint exists. Fetches everything fresh (all EWX docs, all Notion entries, all Discord channels), replaces in-memory state, and pushes every project. Safety net for drift, hard deletes, and missed probes.

**Incremental sync** — every other tick. Two parallel probes:
1. **EWX probe** — `fetch_changed_docs(session, token, last_ewx_check_ms)` paginates `/backend/job` with `filter=lastModification|* > last_ewx_check_ms`. Each returned doc replaces its entry in `state.docs_by_pn[pn][doc_key]` (or is inserted if new). The set of affected `projectNumber`s drives re-aggregation via `aggregate_one_project()`.
2. **Notion probe** — `fetch_changed_notion_pages(data_source_id, last_notion_check_iso)` uses Notion's `last_edited_time` `on_or_after` filter. Returned pages overwrite `state.notion_entries[pn]` verbatim — the cache always mirrors Notion. Self-edits re-appear here on the next tick; that's expected (it keeps the mirror correct) and they yield no diff against the EWX-derived desired state, so no push loop occurs.

After probes, the union of affected projects is pushed via `push_projects()`. Each project: `push_project_to_notion()` diffs against the cache via `meaningful_diffs` and writes on divergence (cache then refreshed from the API response so it perfectly mirrors Notion); `push_project_to_discord_topic()` diffs the topic and PATCHes the channel if EWX or Notion URLs changed. `sync_job_channels()` and `sync_vermietungen_threads()` run only when at least one project was affected.

Each tick:
1. **Capture timestamps before any fetch.** `tick_ms = int(time.time() * 1000)` and `tick_iso = datetime.utcfromtimestamp(tick_ms).isoformat()` are the candidate checkpoints. Captured first so anything modified during the tick is re-probed on the next pass.
2. **Run full_sync or incremental_sync.**
3. **Advance checkpoints only on full success.** `last_ewx_check_ms`, `last_notion_check_iso`, and (on full ticks) `last_full_sync_at_ms` are persisted to `sync_state.json`. Any exception aborts before this step, so the same window is retried next tick.
4. **Re-login is reactive.** If anything in the tick raises, the session is dropped (best-effort logout) and `try_login()` runs at the top of the next tick. No proactive token refresh.

Shutdown: `SIGINT`/`SIGTERM` set `_stop = True`. `_interruptible_sleep()` polls the flag at 1s granularity so Ctrl+C exits promptly. The `finally` clause in `main()` logs out before returning.

`POLL_INTERVAL_SECONDS`, `FULL_SYNC_INTERVAL_SECONDS`, and `SYNC_STATE_FILE` are top-of-file constants.

### Data flow

```
[Daemon startup]
  resolve_notion_data_source_id() → load_sync_state() → load_sync_state_from_disk(dsid)
  → hydrate SyncState from local_eventworx_docs.json, local_notion_projects.json,
    local_discord_channels.json; rebuild state.projects from raw docs

[Each tick] login (lazy) → capture tick_ms, tick_iso

  IF first_tick OR last_full_sync_at expired OR no EWX checkpoint:
    full_sync(session, token, state):
      fetch_all_docs        → replace state.docs_by_pn, rebuild state.projects
      fetch_existing_notion → replace state.notion_entries
      fetch_discord_channels → replace state.discord_by_pn
      sync_job_channels      (over the full project list)
      push_projects(all PNs) → per-project Notion + Discord topic
      sync_vermietungen_threads (over the full project list)
      save_state_to_disk

  ELSE:
    incremental_sync(session, token, state, last_ewx_check, last_notion_check):
      fetch_changed_docs        → apply_changed_docs   → affected EWX PNs
      fetch_changed_notion_pages → apply_changed_notion → affected Notion PNs
      IF affected: push_projects(affected) → per-project Notion + Discord topic
                   sync_job_channels (full list)
                   sync_vermietungen_threads (full list)
                   save_state_to_disk

  advance last_ewx_check_ms = tick_ms, last_notion_check_iso = tick_iso
  on full sync also: last_full_sync_at_ms = tick_ms
  → save sync_state.json
```

### Local cache files (git-ignored)

| File | Purpose |
|---|---|
| `local_eventworx_docs.json` | Raw EWX `Document` records grouped by `projectNumber` and keyed by `docType:docId`. Authoritative input to per-project aggregation on incremental ticks. |
| `local_eventworx_projects.json` | Aggregated `ProjectSummary` snapshot — derived view of `local_eventworx_docs.json`. Human-readable, also bootstraps the in-memory `state.projects` at startup. |
| `local_notion_projects.json` | Perfect mirror of Notion. Each entry stores `page_id`, `last_edited_time`, and the `ProjectSummary` extracted from the page. Refreshed from API responses on every write so cache and Notion never diverge. |
| `local_discord_channels.json` | Snapshot of Discord channels carrying an `[EWX:P-XXXX]` tag. Replaced on full sync; mutated in place on incremental ticks. |
| `local_discord_threads.json` | Snapshot of vermietungen threads after the last sync. |
| `all_eventworx_raw.json` | Raw API response dump from the most recent full sync. Not consumed by the daemon. |
| `sync_state.json` | Daemon checkpoints: `last_full_sync_at_ms`, `last_ewx_check_ms`, `last_notion_check_iso`. Deleting this forces a full sync on next startup. |
| `ewx_api_test_response.json` | Output of `ewxApiTest.py` probes; unrelated to the daemon. |

### Key data model details

- Eventworx prices are in **cents** — divide by 100 to get euros
- Eventworx timestamps are in **milliseconds** — divide by 1000 for Unix seconds
- Both timestamp normalizers truncate to the minute to avoid jitter-based spurious diffs
- `force_notion_sync = True` bypasses the local cache check and always syncs
- `dry_run = True` logs every Discord write (channel topic updates, channel creation, thread create/rename/archive/unarchive) without calling the API. Notion writes still happen. Toggle is at the top of `ewxSync.py`.
- `Document.docId` holds the Eventworx document UUID used to build deep-link URLs
- `ProjectSummary.representativeUrl` is the Eventworx URL for the representative doc — embedded as `text.link` on the Notion `Project Number` field and written into the Discord channel topic EWX tag
- `ProjectSummary.notionUrl` is read from Notion and stored in the cache; never written back or diffed
- `DiscordChannel` stores `channelId`, `channelName`, `projectNumber`, `eventworxUrl` (from topic), and the full raw `topic` string

### Notion database fields

`Title`, `Project Number` (rich_text, hyperlinked to Eventworx representative doc), `Status` (select), `Representative Job` (rich_text), `Representative Type` (select), `Current Price` (number), `Rent` (date range), `Categories` (multi_select), `Has Order/Offer/Request/Delivery/Invoice` (checkboxes)

### Discord channel identification

Relevant channels carry a `[EWX:P-XXXX]` tag in their topic — this is the join key between Discord and Eventworx. The sync script finds these channels via `_EWX_TAG_RE`, extracts the project number and any existing URL, then updates the tag to `[EWX:P-XXXX](eventworx-url)` when the URL is missing or stale. Discord supports `[text](url)` markdown in channel topics.

### Job channel auto-create (non-Technikmiete jobs)

`sync_job_channels()` creates a Discord text channel for each active non-Technikmiete project that lacks one, under the category in `DISCORD_JOBS_CATEGORY_ID`.

- **Target set**: projects with `status == "Aktiv"` AND `"Technikmiete" NOT in categories`.
- **Channel naming**: `YYMMDD title` from the project's `rentStartDate` (e.g. `261205 Sommerfest Müller`), truncated to 100 chars (`build_channel_name`). When `rentStartDate` is missing, the date prefix is omitted. Discord normalizes the name (lowercase, spaces → `-`) on creation.
- **Topic on creation**: pre-populated with `[EWX:P-XXXX](url)` so the per-project topic-update pass is a no-op for the new channel.
- **Lifecycle**: create-only. No archive, move, or delete when a project leaves the target set — channel relocation on completion is a manual step (see `goals.md`).
- Newly created channels are inserted into `discord_by_project` so the subsequent per-project loop sees them.

### Vermietungen threads (Technikmiete jobs)

`sync_vermietungen_threads()` manages one thread per active `Technikmiete` project inside the channel specified by `DISCORD_VERMIETUNGEN_CHANNEL_ID`.

- **Target set**: projects with `status == "Aktiv"` AND `"Technikmiete" in categories`.
- **Thread naming**: `P-XXXX_title`, truncated to 100 chars (`build_thread_name`). The `P-XXXX_` prefix is the join key — parsed via `_THREAD_PREFIX_RE`. Threads in this channel without that prefix are ignored.
- **Reconciliation**:
  - Target project with no active thread → unarchive a matching archived thread if one exists, otherwise create a new public thread (`type=11`, no starter message, 7-day auto-archive).
  - Active thread whose project left the target set → PATCH `archived: true` (never deleted).
  - Active thread still in target set whose name diverges from `build_thread_name(...)` → rename in place (e.g. when the Eventworx title changes).
- Archived threads are only listed on demand (when at least one target project lacks an active thread), to avoid the extra paginated API call when not needed.

### Logging convention

One log line per *change*, one summary line for the *no-ops*. Each subsystem prints a summary at the end of its pass:
- Notion: `Notion: N created, M updated, K unchanged.`
- Discord topics: `Discord topics: N updated, M unchanged, K skipped (no channel).`
- Job channels (`sync_job_channels`) and vermietungen threads (`sync_vermietungen_threads`) print plan/result summaries before and after their action loops.

In dry-run mode every would-be write is prefixed with `[DRY-RUN]` so the log reads identically to a real run minus the API calls.

### Eventworx auth

Login uses `license: READONLY` to avoid consuming a full user license. The `X-AUTH-TOKEN` header from the login response must be included in all subsequent requests. Always call `logout()` — a `try/finally` block ensures this even on error.

## Dependencies

```bash
pip install requests notion-client discord.py python-dotenv
```

Python 3.10+ required (uses `int | None` union syntax in ewxSync.py).
