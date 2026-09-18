# PrivaSheet

**Privacy-first invoice extraction powered by local OCR and local LLMs.**

PrivaSheet turns scanned and photographed invoices into structured JSONL without sending your documents to a cloud
service. Your documents stay on your machine.

```text
upload ─► ingest ─► OCR ─► layout ─┬─► template builder (LLM proposes, you confirm) ─► template
                                   │
                                   └─► extractor (LLM + template) ─► validate ─┬─ passed ──────────┐
                                                                               │                   ▼
                                                                               └─ needs_review ─► review ─► export (JSONL)
```

The AI only points at text that is already on the page. It never invents a value, and when it is not sure it says so
and the document goes to human review.

> **Status: early development.** This README describes the design the project is being built to
> ([`docs/superpowers/specs/2026-09-16-template-extraction-design.md`](docs/superpowers/specs/2026-09-16-template-extraction-design.md)),
> not a shipped product. See [Current state](#current-state) for what exists today.

## Why?

Invoices contain financial, customer and business data that should not be uploaded to a cloud service.

PrivaSheet takes a **local-first** approach:

* **100% local processing** — no cloud AI, no network access outside loopback
* **Light stack** — no PyTorch, no Docling; OCR runs on CPU
* **Evidence-based** — every value is text the OCR actually read, cited by box
* **Human-in-the-loop** — anything uncertain becomes a review, never a silent guess
* **Free and open source (MIT License)**

## How extraction works

The documents PrivaSheet is built for are mostly **scanned paper invoices and phone photos, in English**. They have no
text layer, so text-layer extractors (pdfplumber, Docling) add weight without adding value. Extraction is therefore
**template-based**: you describe the fields once per invoice layout, and every later document of that layout is read
with that template.

| Step | What happens |
|---|---|
| **1. Ingest** | The upload is checked by file signature (PDF, JPEG, PNG, TIFF), size and pixel count, then turned into page images — PDF pages rendered at 200 DPI with pypdfium2, images opened with Pillow, EXIF orientation applied, every TIFF frame read. |
| **2. OCR** | RapidOCR (ONNX Runtime) reads each page and produces an immutable **OCR snapshot**: one box per text fragment, with its text, position and confidence score. |
| **3. Layout** | Boxes are ordered into reading-order lines per page. Deterministic, no AI. |
| **4. Extract** | The local LLM receives the template's fields, your description prompts, the hints saved with the template, and the OCR lines. It answers with **box IDs and text spans only** — or `missing`, or `uncertain` with a reason. |
| **5. Ground** | Code — not the model — turns the cited boxes into a value: the cited box texts are joined, the span must occur in them, and the matched text becomes the raw value. A span that is not found is an `UNGROUNDED_VALUE` issue. |
| **6. Validate** | Raw text is parsed into canonical values (ISO dates, plain decimals) and checked. Any issue sets the status to `needs_review`. |
| **7. Review** | A human sees the page with the cited boxes highlighted, the issue list and the reasons, edits values, and marks the document reviewed or rejected. |
| **8. Export** | Selected documents of a batch are written as JSONL, one line per document. |

### Validation issues

| Code | Meaning |
|---|---|
| `AI_UNCERTAIN` | The model was not sure and said so. The value stays `null` until a human decides. |
| `DUPLICATE_DOCUMENT` | The file's SHA-256 matches a document uploaded earlier. Nothing is extracted. |
| `TEMPLATE_MISMATCH` | Fewer than 90% of the template's key labels were found — probably the wrong template. |
| `REQUIRED_MISSING` | A field or table you marked `#required` is missing. |
| `UNGROUNDED_VALUE` | The span the model returned is not in the boxes it cited. |
| `DUPLICATE_BOX` | Two fields claim the same box and span. |
| `PARSE_ERROR` | A date does not match its format or is not a real date; a decimal does not match the decimal grammar. |
| `LOW_OCR_SCORE` | A cited box was read with less than 90% OCR confidence. |
| `PROCESSING_TIMEOUT` | The document exceeded its time budget; it goes to review, with the stage and progress in plain words. |

Processing errors (`LLM_UNAVAILABLE`, `LLM_INVALID_RESPONSE`, `OCR_FAILED`, `DOCUMENT_UNREADABLE`) set the status to
`failed` and offer a retry.

**Passing every check does not prove correctness** — a grounded value can still come from the wrong place on the page.
That residual risk is measured as *false acceptance* in the benchmark, and the gate for it is zero.

## Creating a template

One template = one layout. A vendor with three invoice layouts needs three templates. Templates are versioned; editing
one saves a new version with a date-based label (`1.1.20260918`), and older versions stay readable.

1. **Upload a sample document.** It is ingested and OCR'd like any other document.
2. **Add the fields you want**, one at a time. Each is either a single value (a *field*) or rows (a *table* with
   columns). For each one you set the key, the type (`text`, `date`, `decimal`), the date format where relevant, and
   write a **description prompt** — your instruction telling the AI what to find, for example
   *"The invoice number printed after 'Invoice No', not the PO number."*
3. **Use hashtag presets** so you do not retype prompts: typing `#invoice_no`, `#due_date`, `#total` or `#line_items`
   fills in the key, type and a starter description you can edit. Presets cover parties, document references, terms,
   amounts and line items. Add `#required` to a field to make it required — without it, the field is optional.
4. **Ask the AI to propose.** The model reads the sample and proposes which OCR box holds each field and each table
   header. A field it is unsure about comes back with a reason and a suggestion to write a more specific prompt.
5. **Confirm on the page.** Every OCR box is drawn as an overlay on the page image. Click a box to assign or reassign a
   field, narrow a span to part of a box (`INV-0042` inside `Invoice No: INV-0042`), and mark the **key labels** used
   to recognise the layout later. You assign boxes and spans; you never draw free regions.
6. **Save.** Hints are derived from what you confirmed — the label text, the region of the page, an example value — and
   stored with the template.

## Scanning a batch

1. Pick a template (the latest version by default) and upload any number of files.
2. Rejected files are reported immediately and create no work. Accepted files are queued; you can add or remove files
   that have not started yet.
3. One worker processes documents one at a time. Before extracting anything it checks the file's SHA-256 against every
   document uploaded before it; a duplicate is committed as `needs_review` with `DUPLICATE_DOCUMENT` and a link to the
   earlier document.
4. Each document ends as `passed`, `needs_review` or `failed`, written in a single commit, and its file is moved to the
   archive. A crash or restart never leaves a half-processed document: unfinished work is requeued and processed from
   the start.

## Reviewing and exporting

The review screen opens any finished document — including ones that passed, through **Edit**. It shows the archived
original, the page image with the cited boxes highlighted, the issue list with reasons, and the values in canonical
form (date picker, plain decimals). You can edit values, add or remove table rows, save (which re-runs the checks),
acknowledge remaining issues, and then mark the document **reviewed** or **rejected**, or delete it.

Export selects per document with a **Select all** box; only `passed` and `reviewed` documents can be selected, so a
batch does not have to be finished before you export part of it. Output is JSONL, one line per document:

```json
{"schema_version":1,"batch_id":"bat_01J…","document_id":"doc_01J…","source_file":"scan_001.jpg","template":{"id":"abc-layout-1","version":2},"human_reviewed":true,"fields":{"invoice_no":"INV-0042","date":"2026-09-01","total":"1284.00","po_no":null},"tables":{"line_items":[{"description":"Paper A4","qty":"2","unit_price":"600.00","amount":"1200.00"}]}}
```

Decimals are strings so nothing is lost to float rounding, dates are ISO-8601, missing values are `null`, and tables
are nested so template keys can never collide with the envelope. There is no CSV export: line items nest naturally, and
the intended consumer of the output is another program or AI.

## Installation

**Requirements**

| | |
|---|---|
| OS | Windows 10/11, Linux or macOS |
| Python | 3.12 or newer |
| LLM server | [Ollama](https://ollama.com/) (default) or any OpenAI-compatible server, e.g. [llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`, listening on loopback |
| Model | A 7–8B instruction model is recommended for accuracy; 1–3B models run on CPU but make more mistakes |
| Disk | A few GB for the model and the OCR models |
| Storage | The data directory must be on **local, unsynchronized** storage (see below) |

**Planned installer.** The installer will prepare everything before first use — Python dependencies, Ollama (checked
and installed, or you are guided to install it), the configured model pulled with `ollama pull`, the OCR models, and a
`.env` file with defaults — and finish with a self-check (OCR on a sample image, one LLM call) that reports what is
missing. It is not built yet.

**From source (today):**

```bash
git clone https://github.com/mekhal/PrivaSheet.git
cd PrivaSheet
python -m venv .venv
.venv/bin/pip install -e ".[dev]"          # Windows: .venv\Scripts\pip
.venv/bin/python -m uvicorn privasheet.web.app:create_app --factory --port 8765
```

Then open `http://127.0.0.1:8765`.

### Configuration

A `.env` file in the installation folder, created by the installer and editable by hand. Environment variables
override it.

| Variable | Default | Meaning |
|---|---|---|
| `PRIVASHEET_HOST` | `127.0.0.1` | Address the web UI listens on |
| `PRIVASHEET_PORT` | `8765` | Port |
| `PRIVASHEET_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Host names accepted in the `Host` header |
| `PRIVASHEET_DATA_DIR` | `temp` in the installation folder | Database, uploads, page images, exports |
| `PRIVASHEET_BASE_URL` | — | OpenAI-compatible endpoint, e.g. `http://localhost:11434/v1` |
| `PRIVASHEET_MODEL` | — | Model name, e.g. `qwen2.5:7b` |
| `PRIVASHEET_DOCUMENT_TIMEOUT_S` | `600` | Time budget per document |

## Limits and conditions

**Upload limits**

| Limit | Default |
|---|---|
| File size | 25 MB |
| Accepted types | PDF, JPEG, PNG, TIFF (checked by file signature, not extension) |
| Pixels per page image | 40 megapixels |
| PDF render resolution | 200 DPI |
| Time budget per document | 10 minutes, after which the document goes to review with `PROCESSING_TIMEOUT` |

There is no limit on the number of files in a batch or pages in a document.

**Who can use the app.** There is **no login in v1**. The app works like a normal web server: whoever can reach
`PRIVASHEET_HOST:PRIVASHEET_PORT` can see and change invoice data — only this machine with the default `127.0.0.1`,
everyone on the network with `0.0.0.0`, and anyone with the URL if it is published on a hosted service.

**Where the data lives.** All application data stays on the machine that runs the app, in one SQLite database plus the
uploaded files, page images and exports. The database holds extracted invoice data and OCR text and is **not
encrypted** in this version — treat it as being as sensitive as the documents themselves. The data directory must be on
local, unsynchronized storage: syncing a database and its WAL file separately can corrupt the copy and uploads your
invoice data. The app refuses to start on a network (UNC) path or under OneDrive; do not point it at Dropbox, Google
Drive or similar folders either. Deleted rows may remain in the database file's free pages until SQLite reuses them.

**The LLM connection** must be loopback: the host of `PRIVASHEET_BASE_URL` has to resolve to `127.0.0.1`, `::1` or
`localhost`, redirects are not followed, and proxy environment variables are ignored. The app cannot verify that the
LLM server itself runs the model locally — some servers can forward to hosted models.

### Not in this version — planned as separate AI components

Quality improvements are deliberately kept **out of the extraction pipeline** and planned as separate AI components,
each with its own interface, so this pipeline stays focused on transcribing what the document says:

| Later component | What it will do |
|---|---|
| Document classification | Pick the template automatically instead of asking the user to choose per batch |
| File and image preparation | Split, merge and straighten files and photos before they reach OCR |
| Numeric checks | Verify that line amounts, subtotals and totals add up — extraction exports a document whose numbers do not add up exactly as written |
| Analysis of the export | Work on the exported JSONL downstream |

Also out of scope for now: handwriting, non-English documents, image dewarping, CSV export, a CLI, user accounts and
permissions, and backup and restore of application data.

## Current state

Merged on `develop` so far: template model and validation, decimal/date parsing and the validation checks, JSONL
export, the SQLite store (schema, migrations, repository), ingest checks and page rendering, reading-order layout, the
evidence/grounding rules, the loopback LLM client, the extractor and the template builder, and the web shell with the
hashtag field editor, the OCR box overlay and the export selection screens.

Not built yet: the OCR unit, the pipeline worker that ties the stages together end to end, the installer, and the
feasibility spike on real (redacted) documents that has to confirm the core bet — that a template built from **one**
sample lets the LLM extract **later** documents of the same layout — before the design is locked in.

## How this project is built

PrivaSheet is developed with an **AI-Driven Development Life Cycle (AI-DLC)**: the AI proceeds when it is confident and
escalates to a human when it is not — the same principle the product applies to documents.

* Work is specified as tasks with explicit acceptance criteria, implemented test-first, and merged only after
  deterministic gates (pytest, ruff, JS unit tests) and an independent AI review pass.
* Reviews are **exception-based**: routine work merges automatically, uncertain or risky work is escalated to the
  maintainer as `needs-human` with the reason and options, and the answer is recorded as a decision with a score.
* Changes to `.github/**`, AI instructions, docs, dependencies, tests and this README are always decided by a human.
* Merges are merge commits only, with trailers recording the task, risk, who decided, who implemented and the review
  run, so `git log` explains how every change was approved.
* Human decisions live in `docs/decisions/`; the AI reads active decisions only.

**Night shift.** Most of the code is written by an unattended local loop on the maintainer's machine: it picks the next
eligible task from a committed task file, has Codex implement it, runs the gates, has Claude review it (docs and UI
work are reviewed by Claude and Codex jointly), merges green tasks into `develop`, and waits out subscription limits.
Anything it cannot decide becomes a `needs-human` escalation. The loop will move to its own repository as a reusable
template for continuous AI development; nothing about it is needed to run PrivaSheet.

## Testing strategy

| Layer | Runs on | LLM | When |
|---|---|---|---|
| Unit tests | GitHub-hosted runners | Fake LLM | Every change |
| Integration tests | GitHub-hosted runners (CPU) | Ollama + small model | Nightly / on demand |
| Accuracy benchmarks | Local machine | 7–8B model | Before release |

The benchmark reports field accuracy, table accuracy, false acceptance, review rate, failure rate, and seconds per page
with OCR and the LLM on the same machine. All test documents in the repository are **synthetic** — GitHub Actions logs
are public, so real documents never enter CI.

## Contributing

This is a public, read-only repository maintained by a single developer. Issues and pull requests are limited to
collaborators. You are welcome to fork it under the MIT License.

## License

MIT
