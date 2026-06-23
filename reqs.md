# ewxSync — Requirements

Status: Active · Updated: 2026-06-23

Migrated from the former `goals.md` (roadmap + bugfix backlog). Behavioral
descriptions of *how* the daemon works live in `info.md`; this file tracks *what*
it must do and *what's left*. Many requirements are **as-built** — reverse-engineered
from `ewxSync.py`. This is a hobby project with no test suite: a requirement's `Done`
flag is ☑ once its implementation exists in the code, and behavior is validated by
using the daemon. Anything that breaks is filed as a bug below rather than blocking
the requirement's `Done` flag.

## Goals
Why this exists. Everything below traces to one of these.

- **G1** — Mirror Eventworx project state into a Notion projects database.
- **G2** — Mirror Eventworx project state into Discord (channels, threads, topics).
- **G3** — Notify the team in Discord about relevant Eventworx changes.
- **G4** — Run as a robust, low-footprint daemon: memory-only, read-only to Eventworx, license-friendly.
- **G5** — Proactively remind the team of actionable, time-driven project conditions *(planned)*.

## Out of scope
What this deliberately will *not* do.

- Writing back to Eventworx — the integration is strictly read-only.
- Notifying on price changes — too noisy.
- Persisting Discord channel IDs — relevant channels are rediscovered each sync via the `[EWX:P-XXXX]` topic tag.

## Requirements
One row each. Use "shall". `Type`: F=function, Q=quality, C=constraint.
Items tagged `(as-built)` describe behavior already implemented in `ewxSync.py`;
`Done` (☑) means the implementing code exists. Known deviations are tracked as bugs.

| ID  | Type | Requirement                                                                                                  | Pri | Goal | Done |
|-----|------|--------------------------------------------------------------------------------------------------------------|-----|------|------|
| R1  | F    | The system shall full-sync on the first tick and at least once per `FULL_SYNC_INTERVAL_SECONDS` (daily). (as-built) | M   | G4   | ☑    |
| R2  | F    | The system shall incrementally sync each `POLL_INTERVAL_SECONDS` (60s), probing EWX `lastModification` and Notion `last_edited_time`. (as-built) | M   | G4   | ☑    |
| R3  | F    | The system shall push only the projects that changed, diffed against the in-memory mirror. (as-built)        | M   | G4   | ☑    |
| R4  | C    | The system shall operate memory-only and shall never read state from disk. (as-built)                        | M   | G4   | ☑    |
| R5  | C    | The system shall access Eventworx read-only via a `READONLY`-license login and shall always log out. (as-built) | M   | G4   | ☑    |
| R6  | F    | The system shall group docs by `projectNumber` into a `ProjectSummary` and classify status as Aktiv / Abgeschlossen / Storniert. (as-built) | M   | G1   | ☑    |
| R7  | F    | The system shall deduplicate offer variants sharing a `jobNumber`, keeping the most recently modified. (as-built) | S   | G1   | ☑    |
| R8  | F    | The system shall override `currentPrice` with the most recently modified eligible invoice's value. (as-built) | S   | G1   | ☑    |
| R9  | F    | The system shall create/update Notion rows and shall never clear a Notion field with empty EWX data (`meaningful_diffs`). (as-built) | M   | G1   | ☑    |
| R10 | F    | The system shall hyperlink the Notion `Project Number` field to the Eventworx representative document. (as-built) | S   | G1   | ☑    |
| R11 | F    | The system shall maintain `[EWX:P-XXXX]` and `[Notion]` tags in each relevant Discord channel topic. (as-built) | M   | G2   | ☑    |
| R12 | F    | The system shall auto-create a Discord channel for each active non-Technikmiete project that lacks one. (as-built) | M   | G2   | ☑    |
| R13 | F    | The system shall maintain one vermietungen thread per active Technikmiete project (create / rename / unarchive / archive). (as-built) | M   | G2   | ☑    |
| R14 | F    | The system shall post a crew role mention on thread creation to auto-subscribe crew members. (as-built)      | S   | G2   | ☑    |
| R15 | F    | The system shall announce each newly created EWX document into the project's Discord destination. (as-built) | S   | G3   | ☑    |
| R16 | F    | The system shall announce document status changes with a per-docType German phrase. (as-built)               | S   | G3   | ☑    |
| R17 | Q    | The system shall provide independent kill-switches `ANNOUNCE_NEW_DOCS` and `ANNOUNCE_STATUS_CHANGES`. (as-built) | S   | G3   | ☑    |
| R18 | C    | The system shall gate every external write behind a `DEBUG` dry-run mode. (as-built)                         | M   | G4   | ☑    |
| R19 | Q    | The system shall apply connect/read timeouts to every Eventworx and Discord HTTP call. (as-built)           | M   | G4   | ☑    |
| R20 | F    | The system shall re-establish the Eventworx session reactively after a failed tick. (as-built)              | S   | G4   | ☑    |
| R21 | C    | The system shall use `projectNumber` as the sole join key linking Eventworx, Notion, and Discord. (as-built) | M   | G4   | ☑    |
| R22 | F    | The system shall fire a send-invoice reminder when a project's equipment is all returned but no final invoice is sent. | C   | G5   | ☐    |
| R23 | F    | The system shall fire an overdue-return warning when a project's `rentEndDate` has passed but equipment is not checked in. | C   | G5   | ☐    |
| R24 | F    | The system shall fire an unpaid-invoice reminder when a final invoice is issued but unpaid for ≥ 3 weeks.    | C   | G5   | ☐    |
| R25 | C    | The system shall persist a fired-reminder ledger so a restart does not re-fire still-open reminders.         | C   | G5   | ☐    |

## Bugs
Deviations from a requirement. `Ref` = the requirement broken. Migrated from the
former `goals.md` bugfix backlog (2026-06-09 code review). Priorities map to the old
labels: P1 → Hi, P2 → Md, P3 → Lo.

| ID  | Bug                                                                                              | Ref | Sev | Done |
|-----|------------------------------------------------------------------------------------------------|-----|-----|------|
| B1  | HTTP calls had no timeouts (daemon could hang). Fixed via `TimeoutSession`.                     | R19 | Hi  | ☑    |
| B2  | `try_login` called `sys.exit(1)` instead of raising for the tick to retry. Fixed; raises now.  | R20 | Hi  | ☑    |
| B3  | Full-sync EWX checkpoint was captured after fetching. Fixed; `now_ms` taken at top of `full_sync()`. | R1  | Md  | ☑    |
| B4  | `activation == "deleted"` docs are treated as live, keeping a project Aktiv forever.           | R6  | Md  | ☐    |
| B5  | Discord calls don't honor 429 / rate limits; a batch-rename tick can 429 and retry-loop.       | R13 | Md  | ☐    |
| B6  | Notion `Rent` payload sends `start: None` when only `rentEndDate` is set; the write crash-loops.| R9  | Hi  | ☐    |
| B7  | Notion checkpoint is second-precision; minute-truncated `last_edited_time` can miss edits.      | R2  | Md  | ☐    |
| B8  | `fetch_changed_docs` offset paging can skip a row at a page boundary during concurrent edits.   | R2  | Md  | ☐    |
| B9  | Invoice price override is not restricted to `finalInvoice` (`docSubType` uncaptured).           | R8  | Md  | ☐    |
| B10 | EWX checkpoint uses the local clock; server clock skew can lose changes until full sync.        | R2  | Md  | ☐    |
| B11 | Required env vars are not validated at startup; failures surface as confusing mid-flight errors.| R4  | Lo  | ☐    |
| B12 | Dry-run thread create/unarchive don't populate `final_state`, mis-reporting announce routing.   | R18 | Lo  | ☐    |
| B13 | `Document.jobCategoryNames` is typed `list[str]` but defaults to `None` with no normalization.  | R6  | Lo  | ☐    |
| B14 | `resolve_notion_data_source_id()` runs before the retry loop, so the daemon crashes if Notion is down at boot. | R4 | Lo | ☐ |

## Todos

The work plan lives in [todo.md](todo.md) — one checklist item per work item, each
referencing the requirement/goal ID it advances.

---
*Pri:* M/S/C (must/should/could). *Sev:* Hi/Md/Lo. IDs are permanent — never reuse.
*Detail files: `reqs/<ID>.md` (e.g. `reqs/G5.md`, `reqs/B9.md`, `reqs/B4.md`).*
