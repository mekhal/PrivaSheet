# Template-based Invoice Extraction — Design

- **Date:** 2026-09-16
- **Status:** Approved — reviewed section by section by the maintainer on 2026-09-17/18 (changes applied: user prompts and hashtag presets, multiple tables and pages, AI must not guess, no numeric checks, process/archive folders with duplicate check, selectable export, reject and edit in review, date-based version labels, network-reachable web server without login, installer sets up the AI, `.env` configuration)
- **Supersedes:** the "pdfplumber / Docling first → CSV" pipeline described in `README.md`

## 1. Context

PrivaSheet extracts structured data from documents without sending them to a cloud service.
The documents it will actually receive are mostly **scanned paper invoices and phone photos, in English**.
They have no text layer, so text-layer extractors (pdfplumber, Docling's PDF parser) add weight without adding value.

This design replaces the extraction pipeline with **template-based extraction**:

1. The user uploads a first sample document.
2. The user creates a document type (a template) and defines the fields to extract, including line items.
3. A local LLM proposes which OCR text boxes hold each field; the user reviews and saves the template.
4. Later documents of the same layout are extracted with the template and the local LLM.

Everything runs on the user's machine. The output is **JSONL**, one file per batch.

### Goals

- Extract header fields and line items from scanned or photographed invoices.
- Stay local: no cloud AI, no network access outside loopback.
- Stay light: no PyTorch, no Docling.
- Never silently produce an unreliable result: anything that fails a check goes to human review.

### Non-goals (this design)

- Automatic template selection or document classification (planned later, with a different AI).
- File and image preparation for AI reading (e.g. splitting or merging files, straightening photos). If skewed or badly photographed documents turn out to be common, a separate AI component may prepare images before extraction.

- Numeric checks: whether line amounts, subtotals and totals add up (planned later, with a different AI).

Planned additions (classification, file and image preparation, numeric checks, later analysis of the exported JSON) are **separate AI components** with their own interfaces. They are not merged into this extraction pipeline.
- CSV export, CLI.
- Handwriting, non-English documents.
- Image dewarping or perspective correction.
- User accounts, login and permissions (anyone who can reach the web address can use the app, §8).
- Backup and restore of application data.
- Secure erasure: deleted rows may remain in the database file's free pages until SQLite reuses them.
- Analysis of exported JSON (planned later, with a different AI).

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | One template = one layout; layouts are never mixed in a template. A vendor may have several templates. A template can be edited later (a new version, §4.1). | Vendors use more than one invoice layout. |
| D2 | The user picks the template for each scan batch. A batch may contain many documents; they are processed one at a time until the batch is done. | Automatic selection is out of scope. |
| D3 | The user defines the field list **and writes, for every field and table, a description (prompt) of what to find**. The AI only locates fields. | Stable output keys across templates; the user, not the AI, decides what each field means. |
| D4 | The LLM runs on every document, using the template as hints (no coordinate alignment). | Input formats are unpredictable; flexibility is preferred over speed. **Note:** if many documents fail because of skew, perspective or poor photos, add a separate image-preparation AI (§1 Non-goals) rather than making this pipeline more complex. |
| D5 | The LLM returns OCR box IDs and text spans only; values are derived from OCR text by code. **When the LLM is not sure, it must say so instead of guessing; the document then goes to human review.** | The LLM cannot introduce text that is not in the document, and never guesses. |
| D6 | Anything that fails validation is sent to human review. | Core product principle. |
| D7 | Stack: `rapidocr` on `onnxruntime`, `pypdfium2`, `pillow`. LLM through an OpenAI-compatible API (Ollama default). | Lightest viable local stack (~110 MB of wheels on Windows). |
| D8 | Local web UI on loopback. No CLI. | Template review is visual. |
| D9 | Export JSONL, one file per batch, one line per document. No CSV. | Line items nest naturally; downstream consumer will be an AI. |
| D11 | Extraction only transcribes the document. No numeric reconciliation (line amounts, subtotal, total); that is a separate future AI. | Keeps this AI focused; checking the numbers is a different job. |
| D10 | All application data (templates, OCR snapshots, batches, results) is stored in an embedded SQLite database, as JSON documents in `TEXT` columns. No JSON data files. Only binary files (uploads, page images) and the JSONL export stay on disk. | Flexible per-template fields like NoSQL, with transactions, no server and no extra dependency (`sqlite3` is in the standard library). `TEXT` JSON needs only SQLite 3.38 and is readable in any SQLite tool. |

## 3. Architecture

```text
upload ─► ingest ─► ocr ─► OCR snapshot (immutable)
                              │
                              ▼
                           layout ─────────────────────────────┐
                              │                                │
                              ▼                                ▼
                     template_builder                      extractor
                  (LLM → box IDs → human review)    (LLM → box IDs + spans)
                              │                                │
                              ▼                                ▼
                    templates (versioned)          validate ─► passed ─► export (JSONL)
                                                       │
                                                       └─► needs_review ─► review ─► export

pipeline: job state, storage, one background worker
web:      loopback UI; calls pipeline only
llm:      OpenAI-compatible client, loopback only
```

### Units

| Unit | Responsibility | Depends on |
|---|---|---|
| `ingest` | Validates an upload and turns it into page images. PDF pages rendered with pypdfium2; images opened with Pillow; EXIF orientation applied; every TIFF frame read. Enforces limits (§7). Never opens URLs. | pypdfium2, Pillow |
| `ocr` | Runs RapidOCR on a page image passed as a NumPy array (never a path or string). Produces an OCR snapshot. | rapidocr, onnxruntime |
| `layout` | Orders boxes into reading-order lines per page using box geometry. Deterministic. Skew correction and candidate row/column grouping are added only if the feasibility spike shows they improve line-item accuracy (§10). | — |
| `llm` | Minimal OpenAI-compatible chat client. Loopback destinations only, no redirects, ignores proxy environment variables, bounded timeout. Requests JSON output. | stdlib or httpx |
| `templates` | Template model, validation, versioned storage. | — |
| `evidence` | Shared by builder and extractor: checks that box references exist and applies the grounding rule (§5.3) to produce `raw` text. | — |
| `template_builder` | Given a snapshot and a field list, asks the LLM (builder response schema, §5.1) for value and label evidence, and returns a draft template with hints derived from confirmed spans. | layout, llm, evidence, templates |
| `extractor` | Given a snapshot and a template, asks the LLM (extractor response schema, §5.3) for evidence per field and per line-item cell. Returns evidence and `raw` only; it does not parse. | layout, llm, evidence, templates |
| `validate` | Parses source text into canonical values, validates canonical values from reviews, and runs all checks (§6). Pure functions: returns values and issues, writes nothing. | templates |
| `export` | Builds the JSONL content for a completed batch. Writes nothing itself. | — |
| `store` | SQLite access: schema and migrations, JSON documents in and out, transactions, revision-checked updates. No business rules. | sqlite3 |
| `pipeline` | Orchestrates jobs and is the **only** caller that writes through `store` or to data files (§5.4): batches, results, templates, snapshots, exports, the single worker, and restart recovery. | all of the above |
| `web` | Local HTTP UI. No business logic; calls `pipeline`. | pipeline |

Each unit is testable alone: `layout`, `validate`, `templates` and `export` need neither OCR nor an LLM; `store` is tested against a temporary database file; `template_builder` and `extractor` are tested with a fake LLM.

## 4. Data model

Templates and extraction data live in one SQLite database, `privasheet.db`, in the data directory (§8). Each table stores one JSON document per row in a `doc TEXT NOT NULL CHECK (json_valid(doc))` column, plus lookup columns. The JSON shapes in §4.1–§4.4 are the `doc` contents. Uploaded files and page images are binary files on disk; the database stores their paths. No application data is kept in JSON or other text files.

| Table | Key | Lookup columns | `doc` |
|---|---|---|---|
| `templates` | `template_id`, `version` | `name`, `created_at` | Template (§4.1) |
| `documents` | `document_id` | plain columns: `sha256`, `source_file`, `path`, `snapshot_id` (FK) | — |
| `snapshots` | `snapshot_id` | `created_at` | OCR snapshot (§4.2) |
| `batches` | `batch_id` | `template_id`, `template_version` (FK to `templates`), `created_at` | Batch manifest (§4.4) |
| `results` | `result_id` | `batch_id` (FK), `document_id` (FK), `status`, `revision`, `job`, `updated_at` | Result (§4.3) |

- Except in `documents`, lookup columns are **generated columns**, e.g. `status TEXT GENERATED ALWAYS AS (json_extract(doc, '$.status')) VIRTUAL`, with indexes where queried. They cannot diverge from `doc`. Foreign-key columns are generated columns with `REFERENCES` constraints (enforced for both `VIRTUAL` and `STORED` generated columns; checked on SQLite 3.49.1).
- Documents are serialized with `json.dumps(..., ensure_ascii=False, allow_nan=False)` and parsed with `json.loads` in `store`.
- At startup `store` refuses to run if `sqlite3.sqlite_version` is below 3.38 or the JSON functions are unavailable.
- Every connection sets `PRAGMA foreign_keys = ON`, `PRAGMA busy_timeout = 5000` and WAL journal mode.
- **Migrations:** the schema version is kept in `PRAGMA user_version`. Each migration (DDL plus any rewrite of `doc` shapes) runs in one transaction that also sets the new `user_version`, so an interrupted migration leaves the previous version intact. If the database version is newer than the app supports, the app refuses to start.

### 4.1 Template

One row per `(template_id, version)` in `templates`. A saved version is never updated (`store` only inserts template rows); editing creates `version + 1`. The integer `version` is the internal key and ordering. Each row also stores `version_label` in the form `1.<n>.<YYYYMMDD>`: the save date on the local machine, and `n` counting earlier saves of the same template on that date (first save of the day `1.0.20260917`, second `1.1.20260917`, first save on the next day `1.0.20260918`). The version is **not** part of the template name. The UI shows `version_label`; wherever a template is chosen, the default is the latest version.

```json
{
  "template_id": "abc-layout-1",
  "version": 2,
  "name": "ABC Co. – Layout 1",
  "created_at": "2026-09-16T10:00:00Z",
  "sample_document_id": "doc_01J…",
  "sample_snapshot_id": "sha256:9f2c…",
  "fields": [
    {
      "key": "invoice_no", "type": "text", "required": true, "key_label": true,
      "description": "The invoice number printed after 'Invoice No', not the PO number.",
      "hint": { "labels": ["Invoice No"], "region": "top-right", "example": "INV-0042" }
    },
    {
      "key": "date", "type": "date", "required": true, "key_label": false, "format": "DD/MM/YYYY",
      "description": "The invoice issue date, not the due date.",
      "hint": { "labels": ["Date"], "region": "top-right", "example": "01/09/2026" }
    },
    {
      "key": "total", "type": "decimal", "required": true, "key_label": true,
      "description": "The final amount to pay including tax.",
      "hint": { "labels": ["Grand Total"], "region": "bottom-right", "example": "1,284.00" }
    }
  ],
  "tables": [
    {
      "key": "line_items", "required": true,
      "description": "One row per purchased item; skip subtotal, tax and total rows.",
      "columns": [
        { "key": "description", "type": "text" },
        { "key": "qty", "type": "decimal" },
        { "key": "unit_price", "type": "decimal" },
        { "key": "amount", "type": "decimal" }
      ],
      "hint": { "header_labels": ["Description", "Qty", "Unit Price", "Amount"], "end_labels": ["Subtotal"] }
    }
  ],
  "match": { "min_key_label_ratio": 0.9 }
}
```

Rules:

- `key` matches `^[a-z][a-z0-9_]{0,39}$`. Field keys and table keys are unique across the template; column keys are unique within their table.
- `description` is the user's prompt telling the AI what to find. Required for every field and table (non-empty, at most 500 characters); optional for columns. It is written by the user, sent to the LLM as instructions, and shown in the review UI.
- `type` is `text`, `date` or `decimal`. `format` is required for `date` and uses the tokens `DD`, `MM`, `MMM`, `YYYY`, `YY`.
- Columns have `required` (default `true`). A row with a required column missing raises `REQUIRED_MISSING`.
- `region` is one of `top-left`, `top`, `top-right`, `left`, `center`, `right`, `bottom-left`, `bottom`, `bottom-right`. It is a hint, not a constraint.
- `key_label: true` means the field's first label is used for the template-match check (§6). A template needs at least one key label.
- A template may have **several tables**, as many as the user defines.
- Documents may have several pages. Evidence may cite boxes on any page, and a table's rows may continue across pages.

### 4.2 OCR snapshot

One row per snapshot in `snapshots`. The page images OCR ran on are files at `pages/<hex digest>/page-<n>.png`, where `<hex digest>` is the snapshot ID without the `sha256:` prefix (a colon is not valid in Windows paths). `snapshot_id` is `sha256` over the decoded page images (canonical RGB bytes plus dimensions), the OCR configuration and the SHA-256 digests of the model files. A snapshot does not belong to a document: identical uploads share one snapshot. Never modified.

Both the template review screen and the result review screen draw overlays on these stored page images, so boxes always match the image they came from.

```json
{
  "snapshot_id": "sha256:9f2c…",
  "engine": {
    "name": "rapidocr", "version": "3.9.2",
    "models": [ { "name": "PP-OCRv6_det_small", "sha256": "…" }, { "name": "PP-OCRv6_rec_small", "sha256": "…" } ],
    "config_sha256": "…"
  },
  "pages": [
    {
      "page": 1, "width": 2480, "height": 3508,
      "boxes": [
        { "id": "p1-b0007", "text": "INV-0042", "quad": [[0.71, 0.08], [0.84, 0.08], [0.84, 0.10], [0.71, 0.10]], "score": 0.97 }
      ]
    }
  ]
}
```

- Box IDs are `p<page>-b<4-digit index>` in RapidOCR output order, unique within a snapshot.
- `quad` coordinates are normalized to 0–1 of the page image as OCR saw it (after EXIF orientation).

### 4.3 Result

One row per document in a batch, in `results`.

```json
{
  "result_id": "res_01J…",
  "batch_id": "bat_01J…",
  "document_id": "doc_01J…",
  "source_file": "scan_001.jpg",
  "snapshot_id": "sha256:9f2c…",
  "template": { "id": "abc-layout-1", "version": 2 },
  "llm": { "model": "qwen2.5:7b", "prompt_version": 1 },
  "status": "needs_review",
  "extracted": {
    "fields": {
      "invoice_no": { "box_ids": ["p1-b0007"], "span": "INV-0042", "raw": "INV-0042", "value": "INV-0042" },
      "po_no": { "missing": true }
    },
    "tables": {
      "line_items": [
        {
          "description": { "box_ids": ["p1-b0030"], "span": "Paper A4", "raw": "Paper A4", "value": "Paper A4" },
          "qty": { "box_ids": ["p1-b0031"], "span": "2", "raw": "2", "value": "2" }
        }
      ]
    }
  },
  "issues": [
    { "code": "LOW_OCR_SCORE", "target": "total", "detail": "box p1-b0052 score 0.71" }
  ],
  "review": null,
  "error": null,
  "revision": 3,
  "job": 1,
  "updated_at": "2026-09-16T10:05:00Z"
}
```

- `extracted` holds the extractor's evidence and `raw`, plus `value` produced by `validate` from `raw`. It is `null` when the document exceeded its time budget (§7). It is written in the same commit as `issues` and the terminal status, and never modified afterwards.
- `value` is canonical: text as-is, dates as ISO-8601 `YYYY-MM-DD`, decimals as a plain decimal string such as `1284.00`. `value` is `null` when `raw` is `null` or parsing fails.
- `revision` increases on every write of the result (§5.4).

**Review.**

- `review` is `null` until a human saves a review. A review is a **complete effective result**, not a patch: `{ "fields": {key: value|null}, "tables": {key: [ {column: value|null} ]}, "acknowledged": { "revision": n, "issues": ["CODE:target", …] }, "reviewed_at": "…" }`.
- It starts as a copy of the canonical `value`s. The reviewer may edit values, type values not in OCR text, and add or remove rows (§9).
- Review values are entered and stored in **canonical form** (ISO dates, plain decimals). They are validated as canonical values, not re-parsed with the template's source format.
- In a review, `null` means "not on the document", equivalent to `missing` in extraction.
- Saving a review reruns all checks except `TEMPLATE_MISMATCH`, `UNGROUNDED_VALUE`, `DUPLICATE_BOX` and `LOW_OCR_SCORE`, which apply to OCR evidence only. A value that is not valid canonical form cannot be saved.
- "Mark reviewed" is a request based on revision *n* whose `acknowledged` lists every issue of that revision and has `revision: n`. It succeeds only if the stored revision is still *n* (§5.4); the status then becomes `reviewed`.
- "Mark rejected" is the same kind of request and sets the status to `rejected`: the human decided the document does not pass. Rejected documents cannot be exported.
- Any document that has finished processing — `passed`, `needs_review`, `reviewed` or `rejected` — can be opened with **Edit**. Saving changed values sets the status to `needs_review` until the human marks it reviewed or rejected again.

- `error` holds `{ "code": "…", "detail": "…" }` when status is `failed`.

Status lifecycle:

```text
queued ─► processing ─► passed
                    ├─► needs_review ─► reviewed
                    │                └► rejected
                    └─► failed ─► (retry) queued

passed / reviewed / rejected ── Edit + Save ──► needs_review
```

### 4.4 Batch manifest

One row per batch in `batches`. Uploaded files are first saved to `process/`, flushed and closed; then the `documents` rows (with the file's SHA-256), the `queued` results and the batch row are inserted in **one transaction**, which is the commit point. Files in `process/`, `archive/` or `pages/` with no database row are orphans and are removed on startup.

```json
{ "batch_id": "bat_01J…", "created_at": "…", "template": { "id": "abc-layout-1", "version": 2 }, "documents": [ { "document_id": "doc_01J…", "result_id": "res_01J…", "source_file": "scan_001.jpg" } ] }
```

The template version is pinned for the whole batch. `documents` order is the processing and export order.

**Editing a batch.** A batch has no size limit. Until a document starts processing, the user may add files (appended to the manifest, same commit rules as above) or remove them (manifest entry, result and document row deleted in one transaction; the file after the commit). A document that is `processing` or finished is **locked**: it cannot be removed from the batch or replaced (it can still be reviewed or deleted from the review screen, §8).

### 4.5 Export line (JSONL)

The user chooses what to export: each document in a batch has a **checkbox**, with **Select all**. Only documents with status `passed` (complete) or `reviewed` can be selected; others are shown but disabled, so a batch does not need to be finished before exporting. The export is written to `exports/<batch_id>-<UTC timestamp>.jsonl` and offered for download, one line per selected document in manifest order. The request names the document IDs; export re-checks every one in a read transaction and refuses the whole export if any is no longer `passed` or `reviewed`. Values come from `review` when present, else from `extracted`.

```json
{"schema_version":1,"batch_id":"bat_01J…","document_id":"doc_01J…","source_file":"scan_001.jpg","template":{"id":"abc-layout-1","version":2},"human_reviewed":true,"fields":{"invoice_no":"INV-0042","date":"2026-09-01","total":"1284.00","po_no":null},"tables":{"line_items":[{"description":"Paper A4","qty":"2","unit_price":"600.00","amount":"1200.00"}]}}
```

- Decimals are strings to avoid float rounding. Dates are ISO-8601. Missing values are `null`.
- Tables are nested under `tables`, so template keys can never collide with envelope keys.
- The export contains no OCR boxes, spans or issues.
- Export is derived output: no result is modified by exporting. Eligibility and values are read in one read transaction, so the file reflects a single consistent state.

The JSONL file is written to a temporary file and atomically replaced.

## 5. Flows

### 5.1 Create a template

1. User uploads one sample document. `ingest` + `ocr` produce a snapshot.
2. User names the template and **adds fields one at a time**. Each is either a single value (a field) or rows (a table with columns). For each one the user sets the key, type, date format where relevant, and writes a `description` prompt of what to find. **Required** is set by the user when creating the template, by adding the `#required` hashtag to the field (without it the field is optional); `REQUIRED_MISSING` (§6) applies only to fields the user marked this way. To avoid retyping prompts, the UI offers **preset suggestions picked by hashtag** (e.g. `#invoice_no`, `#invoice_date`, `#total`, `#line_items`) that fill in key, type and a starter description, which the user can edit before adding. Presets are a built-in list in v1 covering common invoice and procurement data:
   - Parties: `#vendor_name`, `#vendor_address`, `#vendor_tax_id`, `#vendor_contact`, `#buyer_name`, `#buyer_address`, `#buyer_tax_id`, `#ship_to_address`.
   - Document references: `#invoice_no`, `#invoice_date`, `#due_date`, `#po_number`, `#po_date`, `#quotation_no`, `#contract_no`, `#delivery_note_no`, `#delivery_date`, `#receipt_no`, `#project_code`, `#cost_center`.
   - Terms: `#payment_terms`, `#delivery_terms`, `#currency`.
   - Amounts: `#subtotal`, `#discount`, `#tax_rate`, `#tax_amount`, `#withholding_tax`, `#shipping`, `#total`, `#amount_in_words`.
   - Tables: `#line_items` (columns `item_code`, `description`, `qty`, `unit`, `unit_price`, `discount`, `amount`).
   - Flags: `#required`.
3. `template_builder` sends the LLM the field list and the OCR lines from `layout`, and receives the builder response below. Every evidence reference is checked by `evidence` with the same rules as §5.3.

```json
{
  "fields": {
    "invoice_no": { "value": { "box_ids": ["p1-b0007"], "span": "INV-0042" }, "label": { "box_ids": ["p1-b0007"], "span": "Invoice No" } },
    "po_no": { "value": { "missing": true }, "label": null },
    "due_date": { "value": { "uncertain": true, "reason": "Two unlabeled dates near the top." }, "label": null }
  },
  "tables": {
    "line_items": { "header": [ { "box_ids": ["p1-b0021"], "span": "Description" } ], "end": [ { "box_ids": ["p1-b0040"], "span": "Subtotal" } ] }
  }
}
```
4. The UI shows the page with every OCR box as an overlay and the proposed assignment. The user reassigns by clicking boxes and may narrow the span to part of a box's text (e.g. `INV-0042` inside `Invoice No: INV-0042`); the span must satisfy the grounding rule in §5.3. The user marks key labels and confirms. The AI never guesses here either: a field it reports as `uncertain` is shown with its reason and a request to write a more specific `description` prompt (or assign the box by hand), after which the user can run "AI propose" again.
5. On save, hints are derived by code from the confirmed spans: `labels` from the label span; `region` from the centre of the value's first box; `example` from the value span. The template references its sample through `sample_snapshot_id`, whose page images are immutable. The template is stored as `v1`; a new version created from a different sample references that sample's snapshot.

Users assign OCR boxes and spans; they cannot draw free regions.

### 5.2 Scan a batch

1. User picks a template (latest version by default) and uploads any number of files. Each file is streamed into `process/` and validated by `ingest`; rejected files are reported immediately and create no job (§7).
2. Each accepted file becomes a result in `queued`. Files not yet started can be added or removed (§4.4). The single worker processes documents one at a time, in manifest order, until the batch is done.
3. **Duplicate check first.** The worker compares the file's SHA-256 with every document uploaded before it (any batch, including earlier documents of this batch). If it matches, nothing is extracted: the result is committed as `needs_review` with issue `DUPLICATE_DOCUMENT` (the detail names the earlier batch and file), and the file is moved to `archive/` to wait for human review.
4. Otherwise: `ingest` + `ocr` (child process, reusing an existing snapshot with the same ID) → `layout` → `extractor` → `validate`. The outcome — `extracted`, `issues` and status `passed`, or `needs_review` with the reasons (including `PROCESSING_TIMEOUT` after 10 minutes, §7), or `failed` with `error` — is written in **one** result commit (§5.4).
5. After the outcome commit, the file is moved from `process/` to `archive/` and `documents.path` is updated. On startup, a file found in `archive/` whose row still points to `process/` gets its path corrected, so a crash between the two steps is harmless.
6. Every result keeps a link to its archived file and page images for later inspection. In the review screen the human can edit values, save, or delete the document (§8).

### 5.3 Extraction contract

The prompt contains: instructions, the template's fields, tables and hints, and the document's OCR lines from `layout`, one box per entry as `box_id | page | region | text`.

OCR text is untrusted input. The LLM has no tools. The prompt includes each field's `description` as the user's instruction, states that hints may be inaccurate, that a field not present must be reported as missing, and that **the LLM must never guess**: when it is not sure which text is the value, it reports the field as uncertain with a short reason.

The LLM must return:

```json
{
  "fields": {
    "invoice_no": { "box_ids": ["p1-b0007"], "span": "INV-0042" },
    "po_no": { "missing": true },
    "date": { "uncertain": true, "reason": "Two dates without labels; cannot tell which is the issue date." }
  },
  "tables": { "line_items": [ { "description": { "box_ids": ["p1-b0030"], "span": "Paper A4" } } ] }
}
```

Response rules:

- Every field key in the template must be present, as `{box_ids, span}`, `{ "missing": true }` or `{ "uncertain": true, "reason": "…" }`.
- Every table key must be present as an array (possibly empty). Every row must contain every column key of that table, each in one of the same three forms.
- `reason` is a non-empty string of at most 300 characters.
- `box_ids` is a non-empty list without duplicates; `span` is a non-empty string.

Code then:

1. Rejects the response if it breaks the rules above, contains unknown keys, or references box IDs not in the snapshot. One corrected retry is allowed, so at most two attempts in total (§7).
2. Builds `source` = texts of the cited boxes in the cited order joined with a single space, with runs of whitespace collapsed to one space and trimmed.
3. Normalizes `span` the same way. If it occurs in `source` (case-sensitive), `raw` is the text of `source` at its first occurrence; otherwise `UNGROUNDED_VALUE` is recorded and `raw` is `null`.

### 5.4 Concurrency and recovery

The worker and web requests run in the same app process. `pipeline` is the only writer of persistent data.

- **One app instance per data directory**, enforced by an OS file lock held on `privasheet.lock` for the life of the process (not by checking whether the file exists), so there is only ever one worker.
- **Connections.** Each thread (web request threads, the worker) uses its own connection opened by `store`; connections are never shared between threads. Connections use explicit transactions (`isolation_level=None` with `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`).
- **Short transactions.** Every write (batch creation, result commit, review save, template save, deletion) is one `BEGIN IMMEDIATE` transaction that reads what it changes, applies the change and commits. OCR and LLM calls never run inside a transaction.
- **OCR child process** never opens the database. It returns boxes and writes page images to a temporary directory; the parent flushes and moves them into `pages/` **before** committing the snapshot row, so a committed snapshot always has its images.
- **Revision check.** A review write is executed as `UPDATE … WHERE result_id = ? AND revision = ?`; the worker's outcome commit as `UPDATE … WHERE result_id = ? AND revision = ? AND job = ?`. If no row is updated, the write is rejected: the web UI reloads the result, and the worker discards its outcome.
- **Job generation.** Retrying a document increments a `job` counter on the result; the worker commits only if the counter is unchanged, so an outdated run cannot overwrite a newer one.
- **Deletion** of a batch is refused while any of its documents is `queued` or `processing`; deletion of a single document is refused while it is `processing`.
- **Recovery on startup:** orphans are removed (§4.4); every result in `processing` is reset to `queued` and processed from the start, reusing its snapshot if it exists. Because a result's outcome is one commit, there are no partial stages to resume.

## 6. Validation

Checks run after extraction. Every failed check adds an issue; any issue makes the status `needs_review`.

| Code | Check |
|---|---|
| `AI_UNCERTAIN` | The LLM reported a field or cell as uncertain (§5.3). `detail` is the LLM's reason. The value is `null` until a human reviews it. The review screen suggests making the field's `description` prompt more specific in the template (a new version). |
| `DUPLICATE_DOCUMENT` | The file's SHA-256 matches a document uploaded earlier (§5.2). Raised by `pipeline`; nothing is extracted. |
| `TEMPLATE_MISMATCH` | Fewer than `match.min_key_label_ratio` (default **90%**) of key labels are found in the snapshot. A label is found when a box text, lower-cased and whitespace-normalized, has `difflib` similarity ≥ 0.8 with the label or contains it. The UI suggests creating a new template. |
| `REQUIRED_MISSING` | A required field is missing, or a required table has no rows. |
| `UNGROUNDED_VALUE` | A span is not found in its cited boxes (§5.3). |
| `DUPLICATE_BOX` | The same box ID with the same span is used by two different fields or cells. |
| `PARSE_ERROR` | A `date` does not match its `format` or is not a real calendar date, or a `decimal` does not match the decimal grammar below. |
| `LOW_OCR_SCORE` | A cited box has `score` below 0.90 (below 90% OCR confidence). |
| `PROCESSING_TIMEOUT` | The document exceeded its time budget (§7). Raised by `pipeline`, not by `validate`; `detail` states why the document could not be processed automatically. |

**Decimal grammar.** After trimming, optionally strip one currency marker (`$`, `€`, `£`, `¥`, `฿`, or a 3-letter uppercase code) at the start or end, and spaces next to it. The remainder must match `^\(?-?(\d{1,3}(,\d{3})+|\d+)(\.\d{1,4})?\)?$` with balanced parentheses; `(x)` means negative. At most 15 integer digits. `,` is only a thousands separator, so `12,34` is rejected. The value is stored without grouping, e.g. `1284.00`.

**No arithmetic.** This pipeline only transcribes what the document says. It does not check line amounts, subtotals or totals against each other (D11); a document whose numbers do not add up is exported as written. Numeric reconciliation may be added later as a separate AI component (§1).

- The 0.90 OCR score and 0.8 label similarity are starting defaults, tuned by the feasibility spike (§10).
- Passing all checks does not prove correctness (e.g. a grounded value from the wrong field). This residual risk is measured as false acceptance (§10).

## 7. Errors and limits

Upload limits, checked before a job is created:

| Limit | Default | How it is checked |
|---|---|---|
| File size | 25 MB | While streaming the upload; the request is aborted when exceeded. |
| Accepted types | PDF, JPEG, PNG, TIFF | File signature, not extension. |
| Pixels per page image | 40 megapixels | Image dimensions from the header, and PDF page size × render resolution, before decoding or rendering. |
| PDF render resolution | 200 DPI | — |

There is no limit on the number of files in a batch or pages in a document. Long documents are bounded by the time budget below.

**Time budget.** Each document has a time budget of **10 minutes** (`PRIVASHEET_DOCUMENT_TIMEOUT_S`, default 600), measured from the start of processing to the result commit. Rendering and OCR run in a separate child process; LLM requests use the remaining budget as their timeout. When the budget runs out:

1. The child process is killed or the LLM request is aborted.
2. The result is committed with status `needs_review` (not `failed`) and one issue `PROCESSING_TIMEOUT`. Its `detail` explains in plain words why the document could not be processed automatically: the stage that was running (`ocr` or `llm`), progress where known (e.g. page 3 of 8, LLM attempt 2 of 2), and the elapsed time. Example: `OCR did not finish within 10 minutes (page 3 of 8, 600 s). Enter values by hand or retry.`
3. `extracted` is `null`. The review starts with every field `null` and every table empty. The review screen shows page images if the snapshot was committed; otherwise it offers the original file.
4. The worker continues with the next document. The reviewer may instead use the retry action, which requeues the document (§5.4).

Other processing errors set status `failed` with a retry action:

| Code | Cause |
|---|---|
| `LLM_UNAVAILABLE` | Connection refused or the server returned an error. |
| `LLM_INVALID_RESPONSE` | Two attempts failed the response rules (§5.3). |
| `OCR_FAILED` | OCR raised an error or returned no boxes. |
| `DOCUMENT_UNREADABLE` | The file passed the upload checks but could not be decoded. |

Logs record codes, IDs, timings and a readable title for each item — the original file name for documents, the name for templates and batches — so a log line can be matched to what the user sees. Logs never contain OCR text, extracted values or file contents.

## 8. Security, privacy and storage

**Web UI**

- Works like a normal web server: it listens on `PRIVASHEET_HOST:PRIVASHEET_PORT` from the `.env` file (default `127.0.0.1:8765`). Whoever can reach that address can open it: only this machine with the default, everyone on the network with `0.0.0.0`, and anyone with the URL if it is published on a hosted service. The README states this plainly.
- There is **no login in v1**, so every person who can reach the address can see and change invoice data.
- `PRIVASHEET_ALLOWED_HOSTS` (default `127.0.0.1,localhost`) lists the host names accepted in the `Host` header, which blocks DNS-rebinding attacks from other web pages; add the machine's name or IP when sharing on a network.
- State-changing requests require an `Origin` matching the requested host, so other web pages open in the same browser cannot act on the app.
- Document-derived strings (OCR text, values, file names) are inserted into the page as text only, never as HTML.
- Every delete action (document, batch, template) asks for confirmation in a dialog first. Stored documents and exports are served with `Content-Disposition: attachment` except page images shown in the UI, which are served as `image/png` re-encoded by the app.

**LLM client**

- The host of `PRIVASHEET_BASE_URL` must be `127.0.0.1`, `::1` or `localhost`; the client connects only if the resolved address is loopback. Otherwise the app refuses to start.
- No redirects; proxy environment variables are ignored.
- Model name from `PRIVASHEET_MODEL`. The app cannot verify that the LLM server itself runs the model locally (some servers can forward to hosted models).

**Installation sets up the AI**

- The installer prepares everything the app needs before first use: Python dependencies, Ollama (checked, and installed or the user guided to install it), the configured model pulled with `ollama pull`, the OCR models, and a `.env` file with defaults. It ends with a self-check (OCR on a sample image, one LLM call) and reports what is missing.
- The README lists the requirements: supported OS, Python version, disk space for models, RAM, Ollama, and the default model.
- RapidOCR loads models from the installed package or the data directory only. Downloads happen at install time; runtime model downloads are disabled, and a missing model is an `OCR_FAILED` error. The feasibility spike verifies this with the network disconnected.

**Storage**

- This version stores everything on the machine where the customer runs the app.
- Data directory from `PRIVASHEET_DATA_DIR` in the `.env` file; default `temp/` inside the PrivaSheet installation folder (`PrivaSheet\temp`). The user can change it later by editing `.env`.
- Layout: `privasheet.db` (plus SQLite's `-wal` and `-shm` files), `process/` (uploaded, not yet finished), `archive/` (finished, including duplicates), `pages/`, `exports/`, `privasheet.lock`.
- **The data directory must be on local, unsynchronized storage.** Syncing the database and its WAL file separately can corrupt the copy and uploads invoice data. At startup the app refuses a data directory that is a network (UNC) path or lies under a folder named by the `OneDrive`, `OneDriveConsumer` or `OneDriveCommercial` environment variables; the README tells users not to use other sync folders (e.g. Dropbox, Google Drive).
- The database contains extracted invoice data and OCR text and must be treated as sensitive as the documents themselves. It is **not encrypted** in this version (maintainer decision).
- Uploaded files are stored as `process/<document_id>.<ext>` and moved to `archive/<document_id>.<ext>` when finished (§5.2); original names are kept only as `source_file` metadata. The `documents.sha256` column is indexed for the duplicate check.
- Deleting a batch (allowed only when it has no active work, §5.4) deletes, in one transaction and in this order, its results, its batch row, each of its `documents` rows not referenced by a template's `sample_document_id`, and each of its snapshot rows that no remaining result, document or template references.
- Deleting one document from the review screen (refused while it is `processing`) removes, in one transaction, its manifest entry, its result, its `documents` row unless a template uses it as a sample, and its snapshot if nothing else references it.
- The matching files in `process/`, `archive/`, `pages/` and `exports/` are deleted after the commit; any left behind by a crash are removed as orphans on startup. There is no automatic retention in this design.

**Configuration** — a `.env` file in the installation folder, created by the installer and editable by the user (environment variables override it): `PRIVASHEET_HOST` (default `127.0.0.1`), `PRIVASHEET_PORT` (default 8765), `PRIVASHEET_ALLOWED_HOSTS` (default `127.0.0.1,localhost`), `PRIVASHEET_DATA_DIR` (default `temp` in the installation folder), `PRIVASHEET_BASE_URL`, `PRIVASHEET_MODEL`, `PRIVASHEET_DOCUMENT_TIMEOUT_S` (default 600).

## 9. Web UI

HTML, CSS and plain JavaScript served by a small Python web framework; no Node build step. The framework is chosen in the implementation plan.

| Screen | Purpose |
|---|---|
| Templates | List templates with the latest `version_label` (e.g. `1.1.20260917`), open earlier versions, start a new template or edit (saves a new version), delete with confirmation. |
| New template | Upload sample → name → add fields and tables one at a time (hashtag presets + description prompt) → "AI propose" → review page: page image with an SVG overlay of OCR boxes, click to assign or reassign, mark required and key labels → save. |
| Scan batch | Pick template (latest version by default) → upload any number of files → add or remove files not yet started (started and finished files are locked) → per-file progress and status. |
| Review queue | Link to the archived original file; page image with cited boxes highlighted; issue list with reasons (for `AI_UNCERTAIN`, a hint to refine the field prompt in the template; for `DUPLICATE_DOCUMENT`, a link to the earlier document); "Delete document";  editable values in canonical form (date picker, plain decimals; typed values allowed), add or remove table rows → "Save review" reruns checks (§4.3) → acknowledge remaining issues → "Mark reviewed" or "Mark rejected". The same screen opens any finished document, including ones that passed, through an **Edit** button. |
| Export | Pick a batch → checkboxes per document with "Select all" (only `passed` or `reviewed` documents selectable) → download JSONL. |

## 10. Testing and feasibility

**Automated tests** (`pytest`, run in CI):

- Unit tests for `layout`, `evidence`, `validate`, `templates` and `export` using JSON fixtures.
- `template_builder` and `extractor` with a fake LLM, including invalid and ungrounded responses.
- `store`: migrations (including one that fails midway), refusal of a newer schema, JSON round-trip, generated columns, foreign keys, insert-only templates, revision- and job-checked updates.
- Concurrency: a review save and a worker commit on the same result at the same time; the process killed during batch creation and during a result commit.
- `pipeline` commit rules: revision conflicts, job generation, refused deletion, orphan cleanup and restart recovery.
- `ingest` limits and file-signature checks.
- `web` security checks: Host, Origin and token enforcement.
- One `ocr` integration test on a synthetic invoice image drawn with Pillow, marked `slow`.

**Benchmark harness** (local only, not in CI; real documents never enter the repository). Each document has a hand-written ground-truth JSON in the export format. Metrics per run:

- Field accuracy: share of fields whose value equals ground truth.
- Table accuracy per document: rows matched to ground truth in order; counts of matching rows, wrong rows, missing rows and extra rows. A document's table is correct only when all four show no error.
- False acceptance: documents with status `passed` that differ from ground truth in any field or row.
- Review rate: share of documents with status `needs_review`.
- Failure rate: share of documents with status `failed`.
- End-to-end seconds per page and peak RAM with OCR and the local LLM running on the same machine.

**Feasibility spike before implementation.** The main architectural bet is that a template built from **one** sample document lets the LLM extract **later** documents of that layout. The spike tests that bet with a throwaway script:

1. **Data:** at least 2 layouts. For each layout, 1 sample document for template creation and at least 3 other documents for evaluation. Include phone photos, and at least one multi-page table or one document whose OCR merges cells, if available. Add 1–2 documents evaluated against the wrong template. All redacted.
2. **Tuning set:** create each template from its sample document and tune prompts and thresholds (OCR score, label similarity) on the sample documents only.
3. **Freeze** prompts and thresholds.
4. **Evaluate** the other documents and the wrong-template documents with the frozen settings, and report the metrics above per layout.
5. **Also:** compare table accuracy with plain reading-order lines versus skew correction plus row grouping, and confirm OCR runs with the network disconnected.

Gates, confirmed by the maintainer on 2026-09-17, on the evaluation documents:

- False acceptance: 0. Any document with a wrong value must be sent to review (a smoke-test gate; with this sample size it is not a reliability estimate).
- Every wrong-template document is sent to review.
- Every document that exceeds the 10-minute budget is sent to review with a `PROCESSING_TIMEOUT` reason (§7).

Review rate and time per page are reported but are not gates: sending a document to review is always an acceptable outcome.

If a gate fails, this design is revisited before an implementation plan is written.

## 11. Follow-up documents

- Update `README.md`: template-based extraction, JSONL output, removal of pdfplumber/Docling and CSV.
- Record these decisions (D1–D10) in `docs/decisions/`.

Both are maintainer-reviewed changes.
