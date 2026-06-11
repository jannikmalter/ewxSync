# Eventworx `/backend/job` API Analysis

Reverse-engineered reference for the Eventworx backend API. Two kinds of facts live here — treat them differently:
- **Structural facts** (field names, status vocabularies, filter syntax, label tables) — verified against `cache/app.pretty.js` and `cache/locale_de.json`; stable until Eventworx ships a new frontend.
- **Counts and per-doc observations** ("65 offers", "present on ~140 docs") — point-in-time snapshot data (~Jan 2026, 248 documents across 89 projects). Illustrative only; they drift with every new job and were not all taken from the same dump.

## Endpoints used by the daemon

| Endpoint | Method | Notes |
|---|---|---|
| `/backend/login` | POST (form-encoded) | Fields: `license` (`READONLY`), `forceLogoff`, `username`, `password`. Auth token returned in the `x-auth-token` **response header**; body text contains `LICENSE-NOT-AVAILABLE` when the license is taken. |
| `/backend/logout` | POST | Requires `X-AUTH-TOKEN` header (as do all calls below). |
| `/backend/job` | GET | The document list this file documents. Params: `page`/`start`/`limit` (offset pagination), `sort`/`filter` (JSON-encoded, see below), `opts` (`{"calculateStockConflicts": false}` skips an expensive server-side check), `_dc` (cache-buster timestamp). Response envelope: `{"data": [...rows...], "total": <int>}` — `total` is the full match count, not the page size. |
| `/backend/category/tree/job/groups/rent` | GET | Category tree; `children[]` carries `{id, name}` used to resolve `categories` ids on job rows. |
| `/eventworx/resources/locales/<locale>.json` | GET | Static locale table the frontend loads at runtime (no auth needed). Source of `Common.JobStatusMap` below; pull with `helpers/fetch_locales.py`. This instance: `de` only, `en` → 404. |

---

## Document types and job number prefixes

| docType | prefix | count | notes |
|---|---|---|---|
| offer | AN-* | 65 | has `variant` + `variantNumber` fields |
| order | AU-* | 59 | |
| deliverynote | LI-* | 56 | |
| clearance | F-* | 20 | "Freigabe"; statuses: open, returning, finished |
| invoice | RE-* | 27 | also one `TMP_xxxxxxxx` for draft/archived temp invoices |
| repair | REP-* | 15 | |
| request | AF-* | 6 | |

**Clearance (F-\*)** was not previously documented. It represents a release/handover document and can appear as a peer to orders/deliveries within a project.

---

## Common structure across all docs

Every record has: `id`, `jobNumber`, `projectNumber`, lifecycle fields (`status`, `deliveryStatus`, `returnStatus`, `invoiceStatus`), timing (`startDate`, `endDate`, `rentStartDate`, `rentEndDate`, `modificationDate`, `versionDate`, `version`), money totals (`overallPriceValue`, VAT fields, discounts), logistics (`deliveryDate`, `returnDate`, `handoverType`), and nested arrays `jobTimeSlots`, `invoiceDetails`, `downPaymentInvoices`.

### Timestamp types (important!)
- `modificationDate` — **integer**, Unix epoch in **milliseconds**
- `versionDate` — **ISO string** e.g. `"2026-01-29T12:42:22.471Z"` (present only on some docs, otherwise `null`)
- All other date fields (`rentStartDate`, `startDate`, etc.) — **integer milliseconds**

### Status values seen in data
`open`, `draft`, `offered`, `accepted`, `ordered`, `sent`, `rejected`, `cancelled`, `applied`, `returning`, `completed`, `finished`, `fullypaid`

This is only the *occupied* subset — statuses no document currently sits in never appear in a data dump. The authoritative *complete* vocabulary comes from the frontend (below).

### Authoritative status vocabulary

The API response carries only the raw `identifier` in `status` — never a display label — so any human-facing label must be mapped client-side. Three frontend sources are relevant, in increasing authority:

1. **`JobStatus` store** (`cache/app.pretty.js` → `EventWorx.store.lookups.JobStatus`) — each `identifier` → a *generic*, docType-agnostic German `displayText`. A fallback only; the real labels are per-docType.
2. **`JobStatusMap` store** (`cache/app.pretty.js` → `EventWorx.store.lookups.JobStatusMap`) — each `identifier` → `relevants`, the comma-separated docTypes it is valid for. The valid-status-per-docType matrix.
3. **`EwLocales` locale table** — the labels the UI actually renders, and the **authoritative** source. `EwLocales.lookup('Common.JobStatusMap.<docType>', <identifier>)` reads a nested dict loaded at runtime from `<base>/eventworx/resources/locales/<locale>.json` (`EwLocales.key` starts empty; an AJAX GET fills it). Being per-docType, the *same* identifier can render differently by docType (e.g. `sent` → "Gesendet" on an offer but "Bestätigt" on an order). This instance is **German-only** (`de.json`; `en.json` → 404). Pull a fresh copy with `helpers/fetch_locales.py`. `STATUS_PHRASES` in `ewxSync.py` mirrors this tree (German → English) for the docTypes the daemon announces.

#### Authoritative per-docType labels (`Common.JobStatusMap`, from `de.json`)

DocTypes the daemon announces (bold = differs from the generic `JobStatus` label):

| docType | `identifier` = German label (UI) |
|---|---|
| `order` | draft=Entwurf · sent=**Bestätigt** · open=Offen · finished=Abgeschlossen · cancelled=Storniert |
| `offer` | draft=Entwurf · sent=Gesendet · accepted=Angenommen · open=Offen · ordered=Beauftragt · rejected=Abgelehnt |
| `request` | draft=Entwurf · sent=Gesendet · offered=Angeboten · accepted=**Bestätigt** · rejected=Abgelehnt |
| `deliverynote` | draft=Entwurf · planning=**Entwurf** · checkout=**Packen** · picking=Packen · picked=Gepackt · delivered=Geliefert · arrived=Angeliefert · open=In Bearbeitung · returning=**Im Wareneingang** · returned=Ware zurück · finished=**Ware zurück** · partialreturn=Teilw.zurück · completed=Abgeschlossen · overdue=Überfällig · cancelled=Storniert · rejected=Storniert |
| `invoice` | draft=Entwurf · open=Offen · applied=Verrechnet · partiallypaid=Teilzahlung · fullypaid=Bezahlt · rejected=**Storniert** · overdue=Überfällig · reminding=Gemahnt |

Other docTypes (not tracked by the daemon; for reference):

| docType | `identifier` = German label (UI) |
|---|---|
| `repair` | open=Offen · processing=In Arbeit · rejected=Storniert · finished=Abgeschlossen |
| `clearance` | open=In Klärung · rejected=Storniert · finished=Abgeschlossen |
| `purchase` | draft=Entwurf · sent=Angefragt · offered=Angebot erhalten · ordered=Bestellt · processing=Wareneingang · finished=Abgeschlossen · cancelled=Storniert · rejected=Abgelehnt |
| `reminder` | draft=Entwurf · open=Offen · finished=Erledigt · cancelled=Storniert · overdue=Überfällig |
| `bill` | draft=Entwurf · open=Offen · applied=Verrechnet · partiallypaid=Teilzahlung · fullypaid=Bezahlt · rejected=Storniert · overdue=Überfällig · blockpayment=Zahlungssperre · cashdiscountoverdue=Skonto · debiting=Abbuchung · approved=Freigegeben |

**Per-docType gotchas — the same code, different meaning:**
- `sent` is "Gesendet" everywhere **except `order`, where it is "Bestätigt"** (confirmed). An order never displays "Gesendet". `accepted` is "Angenommen" on an offer but **"Bestätigt"** on a request.
- `deliverynote` rental-return lifecycle: `checkout`/`picking`=Packen (packing) → `delivered`=Geliefert → `returning`=Im Wareneingang (being checked back in) → `returned`/`finished`=Ware zurück (fully back) → `completed`=Abgeschlossen. Here `finished` is **"Ware zurück", not Abgeschlossen** — only `completed` closes a deliverynote as "Abgeschlossen". (Corrects an earlier guess that had `checkout`/`returning` swapped to "Im Wareneingang"/"Ware zurück".)
- `planning` (deliverynote) renders as **"Entwurf"** (draft), not a distinct "planned" label.
- `rejected` renders as **"Storniert"** (cancelled) on order/deliverynote/invoice/clearance/repair/bill, but as "Abgelehnt" on offer/request/purchase.
- "Abgeschlossen" maps to `finished` for order/repair/purchase/clearance but to `completed` for deliverynote.

#### `relevants` matrix + generic `JobStatus` fallback labels

From the two `app.pretty.js` stores. The generic label applies only where a docType has no specific locale entry above — always prefer the per-docType table for actual display text.

| identifier | generic label (`JobStatus`) | `relevants` (valid docTypes) |
|---|---|---|
| `draft` | Entwurf | request, offer, order, deliverynote, invoice, bill, purchase |
| `sent` | Gesendet | offer, order, purchase, request |
| `accepted` | Angenommen | request, offer |
| `rejected` | Abgelehnt | offer, invoice, bill, purchase, repair, request |
| `cancelled` | Storniert | order, deliverynote, purchase |
| `offered` | Angeboten | purchase, request |
| `ordered` | Beauftragt | purchase, offer |
| `open` | Offen | repair, order, invoice, bill, clearance |
| `planning` | — | deliverynote |
| `checkout` | — | deliverynote |
| `picking` | Packen | deliverynote |
| `delivered` | Geliefert | deliverynote |
| `returning` | — | deliverynote |
| `returned` | Zurückerhalten | deliverynote |
| `completed` | — | deliverynote |
| `processing` | — | repair, purchase |
| `finished` | Abgeschlossen | repair, order, purchase, clearance |
| `applied` | Verrechnet | invoice, bill |
| `partiallypaid` | Teilgezahlt | invoice, bill |
| `fullypaid` | Bezahlt | invoice, bill |
| `blockpayment` / `approved` / `debiting` | — | bill |
| `reminding` | Gemahnt | — (no `JobStatusMap` entry; locale tree: invoice, bill) |
| `overdue` | Fällig | — (no `JobStatusMap` entry; locale tree: invoice, bill, deliverynote, reminder) |

The two stores and the locale tree don't fully agree — neither one is complete on its own:
- The generic `JobStatus` store has no entry for `planning`, `checkout`, `returning`, `completed`, `processing`, or the bill-only codes — those resolve only via the per-docType `EwLocales` table.
- The generic labels also disagree with the authoritative per-docType ones (e.g. generic `partiallypaid`="Teilgezahlt" vs. invoice "Teilzahlung"; generic `overdue`="Fällig" vs. invoice "Überfällig"; generic `returned`="Zurückerhalten" vs. deliverynote "Ware zurück").
- The locale tree defines per-docType codes absent from the `JobStatusMap` `relevants` data altogether: `picked`, `arrived`, `partialreturn`, `overdue` (deliverynote), `reminding`, `overdue` (invoice/bill), `cashdiscountoverdue` (bill).

### Sub-status fields

These are independent of the main `status` and have their own value sets:
- `deliveryStatus`: `open` / `partial` / `delivered` (present on ~140 docs)
- `invoiceStatus`: `open` / `partiallyinvoiced` / `fullyinvoiced` (present on ~154 docs)
- `deliveryPlanningStatus` / `returnPlanningStatus` ("Status Anlieferung" / "Status Abholung" in the UI): `not_planned` (Ungeplant) / `open` (Offen) / `requested` (Angefragt) / `ackknowledged` (**Bestätigt**). The identifier really is **`ackknowledged` with a double k** — a typo in Eventworx's own code (verified in `app.pretty.js`); exact-match against the misspelled form. *Caution:* "Bestätigt" is ambiguous on an order's detail page. It can be the **main job status** (an order's `status=sent` renders as "Bestätigt" — see the per-docType table above) and/or this **planning sub-status** (`ackknowledged`). They are independent fields with the same German label; don't read one as the other.

---

## Official REST API documentation (api-doc.eventworx.biz)

Eventworx publishes a Swagger UI at `https://api-doc.eventworx.biz/#/`; the underlying spec is `https://api-doc.eventworx.biz/eventworx-api.yaml` (Swagger 2.0, v1.0.0, snapshot saved as `cache/eventworx-api.yaml`). It's clearly generated from the same ExtJS model definitions we mined from `app.pretty.js` — field lists and German descriptions match verbatim. Visibly unfinished (the auth how-to says "todo"; the file still carries the Uber-API example header comment).

**What the official docs add over this analysis:**
- **Auth for REST mode: HTTP header `S-API-TOKEN`** — created in the program configuration area, i.e. the `restapi` app token from `ApiKeyConfiguration` (this answers the open header-name question). Browser mode uses cookies; same API either way.
- **Official write endpoints** (we had only cataloged reads): `PUT /job/{id}?forced=`, `POST /job/update` (batch `CRUDData`), `POST /job/create?forceNewIndexNumber=&updateSource=` — plus search + create/update for `contact`, `article` (incl. `article/update-webshop`), `bundle`, `service`, and `/stock/item/search`. ewxSync stays read-only on EWX by policy, but writes are officially supported.
- **`GET /job/{jobId}`** (single doc) returns `associatedData` (referenced services/contacts) and **`positionTree`** (the job's line items, `EventWorx.model.PositionModel`) — the official way to get per-position data, which the `/job` list response doesn't carry.
- Documented intended use cases: contact sync, invoice export to accounting, schedule pull for staff planning.

**What it confirms from our reverse-engineering** (now official, not inference):
- `/job` list parameters: `key`, `filter`, `sort`, `page`, `start`, `limit`, `opts` — same surface as `/backend/job`. (Note the search param is named `key` here vs. `query` in the webapp's project search.)
- `docSubType` semantics ("invoice + downpayment or invoicecorrection") — verbatim.
- `activation` is "Informatives Feld! Ist der Job (geloescht/archiviert oder aktiv)" — i.e. deleted/archived/active, matching our P2 deleted-handling finding.
- `JobTimeSlot.standardId` "identifies the standard entry, e.g. DISPO".

**What this analysis has that the official docs don't** (keep maintaining it):
- The entire **filter/sort language**: `<field>|<case>` syntax, case OR-ing, operators — the spec types `filter`/`sort` as bare strings with no description.
- The **Solr field names** (`lastModification`, `extraDate1/2/3`, `archived`, `deleted`, `subType`, `activeFrom`) and their response-body mappings.
- The **status vocabularies** and per-docType label semantics (spec just says status "referenziert den Store lookup.JobStatus").
- The real **response envelope** (`{data, total, start, limit}`) — the spec's `JsonResult` documents only `success`/`message`.
- Everything behavioral: active-view semantics, offer-variant dedup, `activeFrom` inference, project grouping.

**Open question**: the spec's `basePath` is `/` with paths like `/job`, but on our instance the cookie-authed equivalents live under `/backend/`. Most likely the same `/backend/*` paths accept `S-API-TOKEN` in place of the session cookie (the doc frames REST mode as the same API with different auth). Verify with one probe once a `restapi` token is created in the UI.

---

## How Eventworx defines "active" docs (from UI filter requests)

These filters are what the Eventworx web UI sends when loading the "active orders" and "active offers" list views. They reveal which fields and values the system itself uses to determine whether a doc is considered active.

### Filter properties used (server-side only — not present in response body)

| filter property | maps to | notes |
|---|---|---|
| `subType\|*` | combination of `dealType` + docType category | multi-value; both `"rent"` and the type (e.g. `"order"`) must match |
| `status\|*` | `status` field in response | exact values vary by docType (see below) |
| `activeFrom\|*` | internal activation date | `activeFrom <= now` — excludes future-dated/not-yet-active docs; **not exposed in response body** |
| `archived\|*` | `activation` field in response | `"false"` corresponds to `activation != "archived"` |

### Filter property syntax: `<field>|<case>`

The suffix after `|` is **not** a wildcard — it is a *case* qualifier that lets a single request mix different filter sets per docType.

- `|*` — the filter applies to every returned row.
- `|case_<name>` — the filter belongs to a named case. All filters sharing the same case name are AND-ed together; different cases are OR-ed.

Example from the "active offers + active orders" view (sorted by modification date):

```json
[
  {"property": "subType|case_offer", "operator": "in", "value": ["offer"]},
  {"property": "status|case_offer",  "operator": "in", "value": ["draft","open","sent"]},
  {"property": "endDate|case_offer", "operator": ">",  "value": 1779467952241},
  {"property": "subType|case_order", "operator": "in", "value": ["order"]},
  {"property": "status|case_order",  "operator": "in", "value": ["draft","open","sent"]}
]
```

This returns rows matching the offer AND-group **or** the order AND-group in a single response.

### Querying by modification time

The response body exposes the field as `modificationDate`, but Solr indexes it under the name **`lastModification`**. Use that name in `sort` and `filter`. Filtering on `modificationDate` directly fails with `undefined field: "modificationDate"`.

Example — jobs modified in the last hour, newest first:

```json
{
  "sort":   [{"property": "lastModification", "direction": "DESC"}],
  "filter": [{"property": "lastModification|*", "operator": ">", "value": <now_ms - 3_600_000>}]
}
```

Known operators across filter properties: `in`, `<`, `>`, `<=`, `>=`, `=`.

### Active status values by docType

| docType | "active" statuses |
|---|---|
| order (AU-*) | `draft`, `sent`, `open` |
| offer (AN-*) | `draft`, `sent`, `open`, `accepted` |

By exclusion, all other statuses (`ordered`, `returning`, `completed`, `finished`, `fullypaid`, `rejected`, `cancelled`, `applied`, `offered`) are considered non-active/closed from the UI's perspective.

### `dealType` field (in response body)

Seen values: `rent` (223 docs), `sale` (5), `null`/absent (20). Only `rent` docs appear in the active orders/offers views. The `dealType` is separate from `docType` — it describes the commercial model, not the document type.

### Cross-referencing the filters against actual data

Comparing a full dump against the filtered active-orders and active-offers responses reveals which filter does what work. (The doc counts below come from an earlier, smaller dump than the docType table at the top — e.g. 46 vs. 65 offers. The *rules* derived here are the takeaway, not the numbers.)

**Orders (AU-*) — 59 total, 7 active:**
The three filters together (`status`, `archived`, `dealType`) fully account for all exclusions. There are zero orders that pass all three and are still excluded. The `activeFrom <= now` filter adds no additional filtering on orders in practice — it likely serves as a guard against future-dated records that don't exist in this dataset.

**Offers (AN-*) — 46 total in full dump, 9 active (but full filtered response likely contains more AN- docs not in the unfiltered dump):**
Status + archived + dealType cover the majority of exclusions. However, 7 offers pass all three criteria yet are still excluded. Analysis of those 7 reveals two additional behavioral rules that the server applies (likely via the `activeFrom` filter):

1. **Event has ended**: 5 of the 7 excluded offers have `endDate <= now` (the rental period is over). Despite still having status `sent`, they no longer appear as active. All 9 active offers have `endDate > now`.

2. **Project already has a live order**: 2 excluded offers belong to projects that already contain a non-archived order (AN-1069 → project P-1193 has AU-1107; AN-1071 → project P-1196 has AU-1122). AN-1071 in particular has a future `endDate` (2026-05-17) and would otherwise qualify — it is excluded solely because its project has a live order.

**Inferred meaning of `activeFrom`**: A server-side computed timestamp, not present in the response body. For offers it appears to encode "is this offer still relevant?" — set to a past value when the offer is pending and the event is upcoming, and to a future/null value once the event ends or an order is placed for the project. For orders, the field does not cause any additional filtering in practice.

---

## Project-wide status: does not exist yet (verified)

Eventworx currently has **no project-level status**. Everything "project" in the UI is derived from the per-doc statuses of jobs sharing a `projectNumber`. Evidence (verified 2026-06-10 against `app.pretty.js` + a live probe):

- **No vocabulary**: no `projectStatus` identifier anywhere in the bundle; no `Projektstatus` locale entry; `Common.JobStatusMap` is strictly per-docType.
- **A dormant `Project` entity exists but carries no data** — see the next section; Eventworx has announced a project-based system, and this looks like its scaffolding.
- **`selectedPhase` (`started`/`running`/`endet`) is a date-window filter** (startDate/endDate vs. a time range), not a status.

### Dormant `Project` entity — likely the announced project system (not live yet)

> Eventworx told us (2026) they plan to ship a project-based system. The frontend already contains what looks like its scaffolding. **None of it is populated in this instance as of 2026-06-10** — ewxSync must keep deriving project status from docs — but when the feature goes live, this entity is probably where a real project-wide status will appear.

**`EventWorx.model.Project`** (`app.pretty.js` ~line 195402), REST proxy `/backend/project` (JSON envelope with `rootProperty: 'data'`, same shape as `/backend/job`). Fields:

| field | type | notes |
|---|---|---|
| `projectNumber` | string | same join key the docs carry |
| `title` | string | |
| `status` | string | **raw string, no vocabulary anywhere** — no lookup store, no locale entries; the UI renders it verbatim in a pill |
| `offerVariant` | string | |
| `startDate` / `endDate` | date (ms) | |
| `editor`, `lang` | string | |
| `customerRefId` / `customerCopy` | ref Contact | copy-on-write customer snapshot, like on jobs |
| `contactRefId` / `contactCopy` | ref Contact | |
| `postalAddressId`, `locationAddressId` | ref Address | |
| `locationContactId`, `locationContactRefId`, `locationContactPersonId` | ref Contact | |
| `locationNotes`, `organizerAPname`, `organizerAPcomm` | string | |
| `customerIsLocation` | boolean | |
| `modificationDate` | date (ms) | |

**`EventWorx.view.project.ProjectManagement`** — a search-driven UI over this entity (BufferedStore, pageSize 50, remote sort/filter). Requests carry `query` (search string) and `opts: {"scrollTo": ...}`; sortable by `number` and `name_sort`. Grid shows a status pill (raw `status` value), `projectNumber`, and title (falls back to `locationName`).

**Live probe results (2026-06-10**, `cache/probe_project.py`**)**: `GET /backend/project` → HTTP 200, `total: 0`, with and without `query` — the collection is empty. And **all 291 rows in the full `/backend/job` dump have `projectId = null`**, i.e. no job is linked to a project entity; `projectNumber` remains the only project grouping.

**How to detect the feature went live** (worth re-checking after Eventworx updates):
1. `GET /backend/project` starts returning rows (`total > 0`) — rerun `cache/probe_project.py`.
2. Job rows start carrying non-null `projectId`.
3. A new frontend bundle defines a status vocabulary for it (grep a fresh `app.pretty.js` for `projectStatus` / a `Project`-scoped `JobStatusMap` analog; re-pull locales via `helpers/fetch_locales.py` and check for new `Common.*Project*` keys).

If/when that happens, ewxSync could read project status directly instead of (or as a cross-check against) `classify_project()`.

### ProjectControlling view (closest analog to our Aktiv/Abgeschlossen)

`EventWorx.view.controlling.ProjectControlling` is EWX's own "project health" dashboard — and it works exactly like ewxSync: by filtering job docs. Its filter sets:

| filter | orders | offers |
|---|---|---|
| base (always) | status `!= draft`, `!= cancelled` | status `!= draft`, `!= rejected`, `!= ordered` |
| "open" toggle | status in `[open, sent]` | status in `[open, sent, accepted]` |
| "inprogress" toggle | same + `startDate < now` | same + `startDate < now` |
| "finished" toggle | status in `[finished]` | status in `[finished]` |
| always | `deleted\|* = "false"` | `deleted\|* = "false"` |

Two takeaways for ewxSync:
- **`accepted` offers count as live** here (confirms `ACTIVE_OFFER_STATUSES` including `accepted`; the job-management active view's `[draft, open, sent]` lacks it only because it also includes drafts — the two views slice differently and our set is the union).
- **Every EWX view filters `deleted|* = "false"` server-side.** The daemon receives docs with `activation = "deleted"` through its unfiltered queries and currently treats them as live — confirmed bug, tracked as P2 in `goals.md`.

---

## Deeper app-bundle findings (2026-06-10 sweep)

Results of a systematic mining pass over `app.pretty.js` for things relevant to the sync daemon.

### `docSubType` — invoice subtypes matter for the price override ⚠️

The Job model documents `docSubType`: *"DocSubType unterscheidet bei einigen Typen. Beispiel: invoice + downpayment or invoicecorrection"*. Observed in the full dump (by `docType, docSubType`): invoice → `finalInvoice` (29), `invoicecorrection` (4), empty (1); repair → `repair`/`service`; all other docTypes empty.

**Risk for ewxSync's invoice price override**: the daemon picks the most recently modified non-cancelled, non-archived invoice — it does not read `docSubType` at all. An `invoicecorrection` (Rechnungskorrektur) with status `applied` is eligible and would win if modified last (near-miss in real data: P-1086's RE-1007 correction was modified within a minute of its sibling final invoice). A `downpayment` invoice (Anzahlung, documented but not yet observed here) would be worse — its `overallPriceValue` is a partial amount. Fix tracked in `goals.md`: capture `docSubType` and restrict the override to `finalInvoice`.

### Solr-queryable field names (server-side) vs. response fields

Collected from the UI's sort/filter builders. The Solr index uses different names than the response body:

| Solr name (use in `sort`/`filter`) | response-body equivalent | notes |
|---|---|---|
| `lastModification` | `modificationDate` | already documented above |
| `extraDate1` / `extraDate2` | `rentStartDate` / `rentEndDate` | UI's "rent" time filters and rentStart/rentEnd sorts use these |
| `extraDate3` | `creationDate` | creation timestamp |
| `startDate` / `endDate` | same | dispo period |
| `subType` | `docType` (+ `dealType`) | multi-value: both `"rent"` and the docType can be required |
| `status` | `status` | |
| `archived` | `activation` | `"false"` ↔ `activation != "archived"` |
| `deleted` | `activation == "deleted"` (boolean field in model) | **every UI view filters `deleted\|* = "false"`** |
| `activeFrom` | *(not in response)* | server-computed relevance date |
| `number` / `name_sort` | `jobNumber` / `title` | sort-only params seen in grids |

`/backend/job` also accepts a free-text `query` param (used by search-driven views).

### Aggregated sub-status fields (value sets from model docs)

The Job model's own field descriptions give fuller value sets than what the data dump showed:
- `deliveryStatus` — *"Aggregierter Lieferstatus"*: `open`, `picking`, `delivered`, `returned`
- `returnStatus` — *"Aggregierter Rück-Lieferstatus"*: `open`, `partial`, `completed`
- `invoiceStatus` — *"Aggregierter Zahlungsstatus"*: `open`, `prepaid`, `partiallypaid`, `fullypaid`
- `substatus` — *"Userdefined Substatus"*: free-form, user-defined per instance

Other model notes: `noDisposition=true` makes a doc behave like a request (no material reserved); `active` is documented as *"Informatives Feld! Ist der Job Aktiv (d.h. wird disponiert)"* — i.e. "is being dispatched", not a lifecycle status; `gigJobId` is an external-service id (GigPlaner); `startDate`/`endDate` *"kann der Mietzeitraum sein oder gesamte Projektlänge"*.

### Auth alternative: app tokens (worth pursuing)

`GET /backend/api/create-app-token?appType=<type>` mints long-lived app tokens; the settings UI (`ApiKeyConfiguration`) offers types **`restapi`**, `webshop`, `crewbrain`, `scanner` and copies the token to the clipboard. The `restapi` type implies an official REST API surface exists. If usable, this would eliminate the READONLY-license login dance entirely (no license contention, no forceLogoff risk, no logout requirement). Next step: create a `restapi` token in the UI and probe what it authorizes (header name unknown — try `X-AUTH-TOKEN` and `Authorization`); ask Eventworx for REST API docs.

### Push channel: scaffolded but dead

A STOMP-over-websocket client library is bundled (`window.Stomp`) and a `shared-token/websocket` route exists in the endpoint map, but **nothing in the bundle ever instantiates the client** (`Stomp.client`/`Stomp.over` are never called) and no `/topic/`-style subscriptions exist. Like the Project entity: infrastructure for a future feature. Until it goes live, polling `lastModification` remains the only change-detection mechanism.

### Other endpoints potentially useful later

| Endpoint | What it is |
|---|---|
| `/backend/jobtodo/list` | Per-job todos: `jobId`, `jobNumber`, `title`, `responsibleContactId`, `dueDate`, `status`, `description`, `lastModification`. Prime future material for Discord (post a job's open todos into its channel). |
| `/backend/notes/list` | Notes attached to artifacts (`relatedArtifactId` + `relatedArtifactType`: "order, offer, article, project…"). |
| `/backend/message` | Internal broadcast messages (`title`, `message`, `isReadBy`, dates). |
| `/backend/operationallog/list` | Free-text ops log: `timeStamp`, `systemId`, `level`, `text`, `userName`. Not a structured change feed; could attribute changes to users, but brittle. |
| `/backend/calendar/sign` → `/backend/noauth/calendar/abbo/new/<signature>` | **Signed no-auth ICS calendar feeds.** The UI base64-encodes a filter config (offers/orders toggles, categories, handling, dealtype, start/end), gets it signed, and the resulting URL serves a calendar subscription without login. Could feed Discord scheduled events or external calendars with zero license cost. |
| `/backend/check/ping`, `/backend/check/version` | Health checks — cheap session-validity probe instead of waiting for a tick to fail. |
| `/backend/job/all-associated/{id}` | All docs associated with a job — server-side resolution of the conversion chain. |
| `/backend/search/all` | Global full-text search. |
| `/backend/programconfig/get-effective-features` | Feature flags for the instance — could reveal when the project system / websocket go live. |

---

## Lifecycle / conversion chain

```
AF-* (request) → AN-* (offer, possibly multiple variants) → AU-* (order) → LI-* (deliverynote) → RE-* (invoice)
```

Links stored in: `originId`, `sourceJobNumber`, `sourceOrderId`, `deliveryNoteId`, `customerRefId`.

Clearance (F-*) and repair (REP-*) docs sit outside this chain — they are not linked via the normal conversion fields.

---

## Project grouping

`projectNumber` (P-xxxx) groups all documents of the same project. A project can contain docs of all types. The richest project in the dataset (P-1122) has offer + clearance + invoice + order + deliverynote.

---

## Version / lifecycle state fields

### `activation`
| value | count |
|---|---|
| `null` | 142 |
| `"archived"` | 99 |
| `"active"` | 6 |
| `"deleted"` | 1 |

### `active` boolean
**Only present on:** deliverynote (49), offer (37), order (2), clearance (1). **Absent entirely** on request, invoice, and repair docs.

### Version ordering fields
- `modificationDate` (int ms) — updated on every save; most reliable recency signal
- `versionDate` (ISO string) — present on some docs; reflects the last version bump
- `version` (int) — increments with each save

---

## Offers / variants

All AN-* docs have `variant` (integer, starts at 1) and `variantNumber` (e.g. `AN-1006-01`, `AN-1006-02`). Higher variant = later proposal.

---

## Pricing fields

`overallPriceValue` is the **net total in cents** — divide by 100 for euros. `overallPriceValueOtherPriceType` is the gross equivalent. `pricesType` is usually `VATexcluded`. VAT is split into `totalVATfull` and `totalVATreduced`.

---

## Time slots (`jobTimeSlots`)

Array of typed planning slots with `standardId`:

| standardId | UI code | German UI name | meaning |
|---|---|---|---|
| DISPO | DS/DZ | Dispo Zeitraum | Disposition period |
| RENT | MZ | Miet Zeitraum | Rental period |
| DELIVER | LI | Lieferung | Delivery |
| RETURN | RU | Rücklieferung | Return delivery |
| CUSTOMER-PICKUP | AH | Abholung | Customer pickup |
| CUSTOMER-RETURN | RG | Rückgabe | Customer return |
| SETUP | AUF | Aufbau | Setup |
| TEAR | AB | Abbau | Tear-down |

(Registry: `addStandard(...)` calls in `app.pretty.js`. A `CUSTOMER-DELIVER` id appears once in a display template but is never registered — likely dead code; don't expect it in data.)

Each slot has `start`/`end` as string-encoded millisecond timestamps plus human-readable `startDateAsText`/`startTimeAsText`.

---

## Customer / address data

`customer` or `customerCopy` embeds a contact snapshot (copy-on-write). Invoices additionally have `invoiceAddress`/`invoiceContact`. References via `customerRefId`, `locationContactRefId`.

---

## Internal / housekeeping fields

`sub-id-gen-*`, `lastSavingIdentifier`, `tempInitialId`, `gigJobId`, `colorCode` — internal caches/helpers. `datevExportTimeStamp` tracks accounting export state. `continuousInvoiceType` controls recurring billing.
