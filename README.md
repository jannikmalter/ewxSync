# ewxSync

Syncs [Eventworx](https://www.eventworx.biz/) job data into Notion and Discord.

A long-running daemon polls Eventworx for changes, diffs them against an in-memory mirror, and pushes only what changed: project rows in a Notion database, channel topics in Discord, auto-created channels and threads, and announcement messages for new and status-changed documents.

## How it works

The daemon runs purely in memory. There is no database and no state file. On startup it begins with an empty `SyncState` and performs a full sync; every restart starts clean.

Each tick (default every 60s):

1. Capture candidate checkpoints (`tick_ms`, `tick_iso`) before fetching, so anything changed mid-tick is re-probed next pass.
2. Run a full sync or an incremental sync.
3. Advance checkpoints only on full success. Any exception aborts the tick and the same window is retried.

**Full sync** runs on the first tick and at least once per day (`FULL_SYNC_INTERVAL_SECONDS`). It refetches all Eventworx documents, all Notion entries, and all Discord channels, replaces the in-memory state, and pushes every project. It is the safety net for drift, hard deletes, and missed probes.

**Incremental sync** runs on every other tick. It probes Eventworx (`/backend/job` filtered by `lastModification > last_check`) and Notion (`last_edited_time on_or_after last_check`), applies the changes to the in-memory mirror, and pushes only the affected projects.

One Eventworx login is reused across ticks and re-established only after a tick fails. Login uses `license: READONLY` to avoid consuming a full user license.

## Sync model

Eventworx documents are grouped by `projectNumber` into `ProjectSummary` objects. `classify_project()` picks a representative document and derives a project status:

- `Aktiv`: a live order or live offer exists
- `Abgeschlossen`: closed
- `Storniert`: cancelled

Active status sets (derived from Eventworx's own filter requests):

- Orders: `{draft, sent, open}`
- Offers: `{draft, sent, open, accepted}`, plus `endDate > now` and no live order in the project

Notable rules:

- **Offer variant dedup**: offer variants share a `jobNumber` (AN-1073-01, AN-1073-02). Only the most recently modified variant is kept before evaluating live offers.
- **Invoice price override**: when a non-cancelled, non-archived invoice exists, its price is used for `currentPrice` instead of the representative document's price. The most recently modified eligible invoice wins.
- **Deal types**: both `rent` and `sale` count as Aktiv. `sale` covers service-only jobs without rented equipment.

## Discord behavior

Channels are joined to Eventworx via an `[EWX:P-XXXX]` tag in the channel topic. The daemon keeps the tag's deep-link and a separate `[Notion](url)` tag current.

- **Job channels** (`sync_job_channels`): one text channel per active non-Technikmiete project, created under `DISCORD_JOBS_CATEGORY_ID`. Create-only; relocation on completion is manual.
- **Vermietungen threads** (`sync_vermietungen_threads`): one thread per active Technikmiete project in `DISCORD_VERMIETUNGEN_CHANNEL_ID`. Threads are created, renamed in place when the title changes, unarchived when a project returns, and archived (never deleted) when a project leaves the active set. A crew role mention auto-subscribes crew members on creation.
- **Announcements**: new documents post `Auftrag AU-1234 wurde erstellt`; status changes post a per-docType German phrase such as `ist abgeschlossen`. Both run on incremental ticks only, so a restart never replays history.

## Configuration

Environment variables, loaded from a `.env` file at the repo root via `python-dotenv`:

| Variable | Purpose |
|---|---|
| `NOTION_TOKEN` | Notion integration token. Required. |
| `DATABASE_ID` | Notion projects database ID. Required. |
| `EVENTWORX_BASE` | Eventworx base URL, e.g. `https://acme.eventworx.eu`. Required. |
| `EVENTWORX_USERNAME` | Eventworx login. Required. |
| `EVENTWORX_PASSWORD` | Eventworx password. Required. |
| `DISCORD_TOKEN` | Discord bot token. Required. |
| `DISCORD_GUILD_ID` | Discord server ID. Required. |
| `DISCORD_VERMIETUNGEN_CHANNEL_ID` | Channel holding one thread per Technikmiete project. Required for thread sync. |
| `DISCORD_JOBS_CATEGORY_ID` | Category for auto-created job channels. Required for channel sync. |
| `DISCORD_CREW_ROLE_ID` | Role mentioned when a thread is created. Optional. |
| `ANNOUNCE_NEW_DOCS` | Enable "wurde erstellt" messages. Default `true`. Optional. |
| `ANNOUNCE_STATUS_CHANGES` | Enable status-change messages. Default `true`. Optional. |
| `DEBUG` | Dry-run mode: log every external write with a `[DRY-RUN]` prefix instead of calling it, and snapshot in-memory state to `cache/` after each tick. Default off. |

## Running

```bash
# Run the daemon
python ewxSync.py

# Dry-run mode
DEBUG=true python ewxSync.py
```

Stop with Ctrl+C or SIGTERM. The daemon logs out of Eventworx cleanly via a signal handler.

No build step, test runner, or linter is configured.

## Dependencies

```bash
# Daemon (talks to Discord via raw REST, no discord.py needed)
pip install requests notion-client python-dotenv

# Helpers only
pip install discord.py jsbeautifier
```

Python 3.10+ required.

## Repository layout

- `ewxSync.py`: the daemon. All sync logic lives here.
- `reqs.md`: goals, requirements, and bug backlog (with detail in `reqs/`).
- `todo.md`: the development plan; each item references the `reqs.md` ID it advances.
- `docs/`: bulky reference material — reverse-engineered Eventworx notes (`eventworx API analysis.md`) and the frontend job-status store excerpt.
- `helpers/`: standalone, manually-run scripts (API probes, locale fetch, Discord channel utilities). None are part of the daemon.
- `cache/`: git-ignored scratch space for generated JSON and JS snapshots. Nothing here is read by the daemon.
