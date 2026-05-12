# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ewxSync** synchronizes Eventworx (event management SaaS) job data into a Notion database. It also contains a Discord bot that mirrors channel events to Notion.

## Running Scripts

```bash
# Activate the virtual environment first
source .venv/bin/activate

# Main sync script (project-level, current)
python ewxSync.py

# Discord → Notion bot
python notionDiscord.py


```

No build step, test runner, or linter is configured.

## Architecture

### Two sync approaches

**`ewxSync.py`** (current, project-level):
- Groups all Eventworx documents by `projectNumber` into `ProjectSummary` objects
- `classify_project()` determines status and picks the representative doc using the same logic Eventworx's UI uses for its "active orders/offers" views — status sets per docType, `activation != archived`, `dealType = rent`, and `endDate > now` for offers
- Status → 3 values: `Aktiv` (live order or offer exists), `Abgeschlossen` (closed), `Storniert` (cancelled)
- Categories are merged across all docs in the project (union, sorted)
- Syncs to Notion using the newer `notion.data_sources` API (notion-client v3)
- `meaningful_diffs()` prevents overwriting existing Notion values with empty/null data from Eventworx

Active status sets (derived from Eventworx's own filter requests, see `eventworx API  analysis.md`):
- Orders: `{draft, sent, open}`
- Offers: `{draft, sent, open, accepted}` — plus `endDate > now` and no live order in the project

**Offer variant deduplication**: multiple offer variants share the same `jobNumber` (e.g. AN-1073-01, AN-1073-02) and both appear as separate rows in the API response, both with `activation=null`. Before evaluating live offers, `classify_project()` deduplicates by `jobNumber`, keeping only the most recently modified variant. This prevents a rejected variant 2 from being hidden while the older variant 1 (still `sent`) incorrectly marks the project as Aktiv.

### Data flow (ewxSync.py)

```
Eventworx login → fetch categories → fetch all docs (paginated, 50/page)
  → aggregate_projects() → compare with local_eventworx_projects.json
  → fetch existing Notion entries → upsert changed projects → save local cache
```

### Local cache files (git-ignored)

| File | Purpose |
|---|---|
| `local_eventworx_projects.json` | Previous sync state for ewxSync.py |
| `local_notion_projects.json` | Snapshot of current Notion DB state |
| `all_eventworx_raw.json` | Raw API response dump (written every run) |

### Key data model details

- Eventworx prices are in **cents** — divide by 100 to get euros
- Eventworx timestamps are in **milliseconds** — divide by 1000 for Unix seconds
- Both timestamp normalizers truncate to the minute to avoid jitter-based spurious diffs
- `force_notion_sync = True` bypasses the local cache check and always syncs

### Notion database fields (ewxSync.py)

`Title`, `Project Number` (rich_text), `Status` (select), `Representative Job` (rich_text), `Representative Type` (select), `Current Price` (number), `Rent` (date range), `Categories` (multi_select), `Has Order/Offer/Request/Delivery/Invoice` (checkboxes)

### Eventworx auth

Login uses `license: READONLY` to avoid consuming a full user license. The `X-AUTH-TOKEN` header from the login response must be included in all subsequent requests. Always call `logout()` — a `try/finally` block ensures this even on error.

## Dependencies

```bash
pip install requests notion-client discord.py
```

Python 3.10+ required (uses `int | None` union syntax in ewxSync.py).
