# ewxSync Goals

Tracking the broader integration goals across Eventworx, Notion, and Discord.

## Identity & sync model

- **Project number is the single source of truth** linking Eventworx, Notion, and Discord.
- **Every sync pass retrieves the full list from all three services**, then diffs locally and only pushes changes to Notion and Discord (Eventworx is read-only).
- **Discord join key**: every relevant channel carries a `[EWX:P-XXXX]` tag in its topic; the tag is what matches the channel to its Eventworx project. No need to persist Discord channel IDs anywhere — they're rediscovered each sync.
- Local cache files: `local_eventworx_projects.json`, `local_notion_projects.json`, `local_discord_channels.json`.

## Cross-system linking

- [x] **Retrieve Eventworx job URL** for each job/project (build canonical URL from API fields).
  - URL on `representativeUrl` in `ProjectSummary`, embedded as `text.link` on the Notion `Project Number` field.
- [x] **Populate Discord channel topic EWX tag with Eventworx link.**
  - Tag format: `[EWX:P-XXXX](eventworx-url)`. Updated each sync if the URL is missing or stale.
- [ ] **Retrieve Discord channel URL** for each channel (`https://discord.com/channels/{guild_id}/{channel_id}`).
- [ ] **Store Discord channel URL in Notion** — field TBD (not Project Number, which links to Eventworx).
- [ ] **Add Notion page link to Discord channel topic** alongside the EWX tag.
  - Discord supports `[text](link)` natively in channel topics.

## Discord channel lifecycle

- [ ] **Auto-create a Discord channel** when a new Eventworx project appears (mirrors the Notion auto-create behavior).
- [ ] **Channel naming**: `YYMMDD Title`
  - Date = first day of rent in Eventworx (rent start date).
  - Title = the Eventworx project title.
  - Example: `261205 Sommerfest Müller`.
- [ ] **Sync changes to Discord**: when the rent start date or project title changes in Eventworx, rename the Discord channel accordingly.
- [ ] **Archive on completion or cancellation**: move channel into an archive category once the Eventworx job status is `Abgeschlossen` or `Storniert`.

## Change notifications in Discord

- [ ] **Post a message in the channel when an Eventworx job changes.** Detected by comparing the current Eventworx state to `local_eventworx_projects.json`.
  - Notify on: job name, job date (rent start/end), job status, new documents created (orders, offers, invoices, etc.).
  - **Do not notify on price changes** — too noisy.
