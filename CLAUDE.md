# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ewxSync** synchronizes Eventworx (event management SaaS) job data into Notion and Discord. Each sync pass fetches fresh data from all three services, diffs locally, and pushes only changes.

`notionDiscord.py` is an old prototype (Discord bot that mirrored new channels to Notion) and is no longer the active approach. All sync logic lives in `ewxSync.py`.

## Running Scripts

```bash
# Activate the virtual environment first
source .venv/bin/activate

# Main sync script
python ewxSync.py
```

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

### Data flow

```
Eventworx login → fetch categories → fetch all docs (paginated, 50/page)
  → aggregate_projects() → save local_eventworx_projects.json

Notion: fetch existing entries → save local_notion_projects.json
Discord: fetch tagged channels → save local_discord_channels.json

For each project:
  → diff against Notion  → upsert Notion page
  → diff against Discord → update channel topic if EWX link changed

After per-project sync:
  → sync_vermietungen_threads(projects)
    → create/unarchive/rename threads for Aktiv+Technikmiete projects
    → archive threads whose project left the target set
    → save local_discord_threads.json
```

### Local cache files (git-ignored)

| File | Purpose |
|---|---|
| `local_eventworx_projects.json` | Previous sync state for ewxSync.py |
| `local_notion_projects.json` | Snapshot of current Notion DB state |
| `local_discord_channels.json` | Snapshot of current Discord channel state |
| `local_discord_threads.json` | Snapshot of vermietungen threads after the last sync |
| `all_eventworx_raw.json` | Raw API response dump (written every run) |

### Key data model details

- Eventworx prices are in **cents** — divide by 100 to get euros
- Eventworx timestamps are in **milliseconds** — divide by 1000 for Unix seconds
- Both timestamp normalizers truncate to the minute to avoid jitter-based spurious diffs
- `force_notion_sync = True` bypasses the local cache check and always syncs
- `Document.docId` holds the Eventworx document UUID used to build deep-link URLs
- `ProjectSummary.representativeUrl` is the Eventworx URL for the representative doc — embedded as `text.link` on the Notion `Project Number` field and written into the Discord channel topic EWX tag
- `ProjectSummary.notionUrl` is read from Notion and stored in the cache; never written back or diffed
- `DiscordChannel` stores `channelId`, `channelName`, `projectNumber`, `eventworxUrl` (from topic), and the full raw `topic` string

### Notion database fields

`Title`, `Project Number` (rich_text, hyperlinked to Eventworx representative doc), `Status` (select), `Representative Job` (rich_text), `Representative Type` (select), `Current Price` (number), `Rent` (date range), `Categories` (multi_select), `Has Order/Offer/Request/Delivery/Invoice` (checkboxes)

### Discord channel identification

Relevant channels carry a `[EWX:P-XXXX]` tag in their topic — this is the join key between Discord and Eventworx. The sync script finds these channels via `_EWX_TAG_RE`, extracts the project number and any existing URL, then updates the tag to `[EWX:P-XXXX](eventworx-url)` when the URL is missing or stale. Discord supports `[text](url)` markdown in channel topics.

### Vermietungen threads (Technikmiete jobs)

`sync_vermietungen_threads()` manages one thread per active `Technikmiete` project inside the channel specified by `DISCORD_VERMIETUNGEN_CHANNEL_ID`.

- **Target set**: projects with `status == "Aktiv"` AND `"Technikmiete" in categories`.
- **Thread naming**: `P-XXXX_title`, truncated to 100 chars (`build_thread_name`). The `P-XXXX_` prefix is the join key — parsed via `_THREAD_PREFIX_RE`. Threads in this channel without that prefix are ignored.
- **Reconciliation**:
  - Target project with no active thread → unarchive a matching archived thread if one exists, otherwise create a new public thread (`type=11`, no starter message, 7-day auto-archive).
  - Active thread whose project left the target set → PATCH `archived: true` (never deleted).
  - Active thread still in target set whose name diverges from `build_thread_name(...)` → rename in place (e.g. when the Eventworx title changes).
- Archived threads are only listed on demand (when at least one target project lacks an active thread), to avoid the extra paginated API call when not needed.

### Eventworx auth

Login uses `license: READONLY` to avoid consuming a full user license. The `X-AUTH-TOKEN` header from the login response must be included in all subsequent requests. Always call `logout()` — a `try/finally` block ensures this even on error.

## Dependencies

```bash
pip install requests notion-client discord.py python-dotenv
```

Python 3.10+ required (uses `int | None` union syntax in ewxSync.py).
