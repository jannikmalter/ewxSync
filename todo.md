# Todos

The development plan. Each item references the `reqs.md` ID (or goal) it advances.
`reqs.md` is the source of truth for *what's decided*; this file is the *plan of work*.

## Watching Eventworx (re-check after EWX frontend updates)
- [ ] Read project status from EWX `/backend/project` when the project entity goes live. (R6)
- [ ] Switch to app-token auth (`appType=restapi`, `S-API-TOKEN` header) instead of the READONLY login. (R5)
- [ ] Push instead of polling via the bundled STOMP `shared-token/websocket` route. (R2)
- [ ] Signed no-auth ICS calendar feeds → Discord scheduled events / shared calendars. (G2)
- [ ] Post open job todos into the project channel/thread (`/backend/jobtodo/list`). (G3)
- [ ] Proactive session health check via `/backend/check/ping`. (R20)

## Cross-system linking
- [ ] Retrieve the Discord channel URL for each channel. (G2)
- [ ] Store the Discord channel URL in Notion (field TBD). (G1)

## Discord channel lifecycle (done for threads, open for job channels)
- [ ] Rename job channels when rent start date or title changes. (R12)
- [ ] Archive/move job channels on completion or cancellation. (R12)

## Change notifications
- [ ] Announce job name changes. (R16)
- [ ] Announce job date changes (rent start/end). (G3)

## Derived-status reminders & persistence (see `reqs/G5.md`)
- [ ] Implement send-invoice reminder. (R22)
- [ ] Implement overdue-return warning. (R23)
- [ ] Implement unpaid-invoice reminder. (R24)
- [ ] Build the SQLite persistence layer for the fired-reminder ledger (and future query surface). (R25)

## Done (migrated, already shipped)
- [x] Retrieve Eventworx job URL per project, embedded on the Notion `Project Number` field. (R10)
- [x] Populate the Discord topic EWX tag with the Eventworx link. (R11)
- [x] Add the Notion page link to the Discord channel topic. (R11)
- [x] Auto-create Discord channels / vermietungen threads for new projects. (R12, R13)
- [x] Channel/thread naming with `YYMMDD` / `DD.MM.` date prefix from rent start. (R12, R13)
- [x] Announce new documents and document status changes. (R15, R16)
