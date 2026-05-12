# Eventworx `/backend/job` API Analysis

Based on 248 documents across 89 projects (5 paginated pages of 50, last page 48).

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

### Sub-status fields
- `deliveryStatus`: `open` / `partial` / `delivered` (present on ~140 docs)
- `invoiceStatus`: `open` / `partiallyinvoiced` / `fullyinvoiced` (present on ~154 docs)

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

The `|*` suffix is Eventworx's filter path wildcard notation.

### Active status values by docType

| docType | "active" statuses |
|---|---|
| order (AU-*) | `draft`, `sent`, `open` |
| offer (AN-*) | `draft`, `sent`, `open`, `accepted` |

By exclusion, all other statuses (`ordered`, `returning`, `completed`, `finished`, `fullypaid`, `rejected`, `cancelled`, `applied`, `offered`) are considered non-active/closed from the UI's perspective.

### `dealType` field (in response body)

Seen values: `rent` (223 docs), `sale` (5), `null`/absent (20). Only `rent` docs appear in the active orders/offers views. The `dealType` is separate from `docType` — it describes the commercial model, not the document type.

### Cross-referencing the filters against actual data

Comparing the 248-doc full dump against the filtered active-orders and active-offers responses reveals which filter does what work.

**Orders (AU-*) — 59 total, 7 active:**
The three filters together (`status`, `archived`, `dealType`) fully account for all exclusions. There are zero orders that pass all three and are still excluded. The `activeFrom <= now` filter adds no additional filtering on orders in practice — it likely serves as a guard against future-dated records that don't exist in this dataset.

**Offers (AN-*) — 46 total in full dump, 9 active (but full filtered response likely contains more AN- docs not in the unfiltered dump):**
Status + archived + dealType cover the majority of exclusions. However, 7 offers pass all three criteria yet are still excluded. Analysis of those 7 reveals two additional behavioral rules that the server applies (likely via the `activeFrom` filter):

1. **Event has ended**: 5 of the 7 excluded offers have `endDate <= now` (the rental period is over). Despite still having status `sent`, they no longer appear as active. All 9 active offers have `endDate > now`.

2. **Project already has a live order**: 2 excluded offers belong to projects that already contain a non-archived order (AN-1069 → project P-1193 has AU-1107; AN-1071 → project P-1196 has AU-1122). AN-1071 in particular has a future `endDate` (2026-05-17) and would otherwise qualify — it is excluded solely because its project has a live order.

**Inferred meaning of `activeFrom`**: A server-side computed timestamp, not present in the response body. For offers it appears to encode "is this offer still relevant?" — set to a past value when the offer is pending and the event is upcoming, and to a future/null value once the event ends or an order is placed for the project. For orders, the field does not cause any additional filtering in practice.

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

| standardId | meaning |
|---|---|
| DISPO | Disposition period |
| RENT | Rental period |
| DELIVER | Delivery |
| RETURN | Return delivery |
| CUSTOMER-PICKUP | Customer pickup |
| CUSTOMER-RETURN | Customer return |
| SETUP | Setup |
| TEAR | Tear-down |

Each slot has `start`/`end` as string-encoded millisecond timestamps plus human-readable `startDateAsText`/`startTimeAsText`.

---

## Customer / address data

`customer` or `customerCopy` embeds a contact snapshot (copy-on-write). Invoices additionally have `invoiceAddress`/`invoiceContact`. References via `customerRefId`, `locationContactRefId`.

---

## Internal / housekeeping fields

`sub-id-gen-*`, `lastSavingIdentifier`, `tempInitialId`, `gigJobId`, `colorCode` — internal caches/helpers. `datevExportTimeStamp` tracks accounting export state. `continuousInvoiceType` controls recurring billing.
