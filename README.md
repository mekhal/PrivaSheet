# PrivaSheet

**Privacy-first PDF-to-CSV extraction powered by local tools and local LLMs.**

PrivaSheet converts PDF documents into structured CSV files without sending your data to a cloud service.

Your documents stay on your machine.

```text
PDF ─► Extract (pdfplumber / Docling) ─► Validate ─► CSV
            │                               ▲    │
            │ unstructured or not           │    └── fails ─► Exception ─► Human Review
            │ extractable                   │
            └─► Local LLM ─► JSON ──────────┘
```

## Why?

Sensitive documents can contain financial, customer, or business data that should not be uploaded to a cloud service.

PrivaSheet takes a **local-first** approach:

* **100% local processing**
* **No cloud AI required**
* **Deterministic extraction first, LLM only where needed**
* **Human-in-the-loop exception handling**
* **Free and open source (MIT License)**

## How it works

> **Target design.** The project is in early development — the pipeline below describes what is being built, not what is shipped today.

1. **Extract** — tables and text are pulled from the PDF with deterministic tools (pdfplumber, Docling). Numbers taken directly from a table cannot be hallucinated.
2. **LLM fallback** — only content the extractors cannot structure is sent to a local LLM, which returns JSON.
3. **Validate** — every row is checked against a schema and business rules.
4. **Exception → Human Review** — when a document or value cannot be processed confidently, PrivaSheet raises an exception for human review instead of silently producing an unreliable result.
5. **CSV** — validated rows are written to CSV.

### LLM backend

PrivaSheet talks to the LLM through an **OpenAI-compatible API**, so any compatible local server works:

| Backend | Notes |
|---|---|
| [Ollama](https://ollama.com/) | Default. Easiest to install. |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) (`llama-server`) | Lighter; loads GGUF models directly. |

Configuration (planned):

| Variable | Example |
|---|---|
| `PRIVASHEET_BASE_URL` | `http://localhost:11434/v1` |
| `PRIVASHEET_MODEL` | `qwen2.5:7b` |

Small models (1–3B) run on CPU; 7–8B models are recommended for accuracy.

## Status

**Early development.**

The project is currently focused on building and validating the core extraction and exception-handling pipeline.

Production-readiness claims will be backed by tests and measurable benchmarks as the project matures.

## Development process: AI-DLC with Autopilot

PrivaSheet is built using an **AI-Driven Development Life Cycle (AI-DLC)**. The goal is not simply to generate code with AI, but to build AI-assisted systems with explicit engineering practices for architecture, quality, testing, security, and traceability.

The same principle the product applies to documents applies to its own development: **the AI proceeds when confident and escalates to a human when it is not.**

### The loop

1. An issue is opened (Story / Improvement / Task).
2. The agent posts a plan and explicit Acceptance Criteria (AC).
3. The plan is reviewed.
4. The agent opens a **Test PR** with failing tests for the AC only.
5. The Test PR is reviewed.
6. The agent opens a separate **Code PR**.
7. The Code PR is reviewed and merged into `develop`.

### Review policy: exception-based

Reviews at steps 3, 5, and 7 are performed by an independent AI reviewer. Work that is risky or uncertain is escalated to the maintainer. This policy is adopted from day one and recorded as `DEC-0001`.

| Tier | Decided by | Examples |
|---|---|---|
| 🔴 **Always human** | Scripted rules the AI cannot override | PRs targeting `main`; changes to `.github/**`, `CLAUDE.md`, AI-DLC docs, skills, or existing decisions; new or upgraded dependencies; security scan findings; deleted or skipped tests; PRs over 400 changed lines; new network access outside localhost; issues created by the AI |
| 🟡 **AI reviewer** | A separate AI pass; escalates when in doubt | Ambiguous AC; work beyond what was requested; tests exceeding the AC; duplicated code; unstated assumptions; changes to validation or human-review thresholds |
| 🟢 **Automatic** | CI + reviewer approval | Everything else — merged into `develop` automatically |

### Human-in-the-loop

When the AI escalates, it labels the issue `needs-human` and comments with the reason and options:

```text
🔶 needs-human: [type: plan | test | merge]
Reason: <rule or reviewer concern>
Options: A) ... (recommended)  B) ...
Reply: decision: A  score: 1-5
```

The maintainer's reply is recorded as a decision with an evaluation score, and work resumes immediately.

### Autopilot

| Mechanism | Behaviour |
|---|---|
| **Dispatcher** (default) | Runs every 2 hours and advances one issue by one step. Skips without calling the AI when there is no work. |
| **`ai:fast` label** | Chains steps immediately for that issue, up to 6 steps. |
| **`AUTOPILOT` variable** | `off` · `dry-run` (comment only, no merge) · `on-limited` (auto-merge Tasks only) · `on` |

Issue states: `ai:ready` → `ai:planning` → `ai:testing` → `ai:coding` → `ai:reviewing` → `ai:done`, with `needs-human` pausing at any step.

A weekly report tracks the auto-merge to escalation ratio (target 80:20), AI mistakes per decision, and pending escalations.

### Decisions and knowledge

Every human decision is stored in `docs/decisions/` with a lifecycle:

```text
proposed ──► active ──► deprecated
               │
               └──► superseded (points to the new decision)
```

* The AI uses **active decisions only**, read from a generated `docs/decisions/ACTIVE.md`.
* Only the maintainer deprecates or supersedes a decision. The AI may propose it.
* Conflicting active decisions are always escalated.
* Deprecated skills are moved out of `.claude/skills/` so they can never be loaded.
* When the AI violates an active decision, the work is corrected to match it and the mistake is logged.

### Traceability

* **Merge commits only** — no squash or rebase merges, so every commit is preserved.
* Every commit references its issue.
* Every merge commit records how the decision was made:

```text
Issue: #12
Test-PR: #33
Decisions: DEC-0003, DEC-0007
Risk: green
Decided-by: ai:reviewer
Review-run: <workflow run URL>
```

For example, `git log --merges --grep "DEC-0007"` lists all work that relied on a decision.

### Branching

| Branch | Who merges |
|---|---|
| feature branches | Test PRs and Code PRs are opened here |
| `develop` | The AI (low-risk work) or the maintainer |
| `main` | The maintainer only — releases are human-only |

## Testing strategy

| Layer | Runs on | LLM | When |
|---|---|---|---|
| Unit tests | GitHub-hosted runners | Mocked | Every PR |
| Integration tests | GitHub-hosted runners (CPU) | Ollama + small model | Nightly / on demand |
| Accuracy benchmarks | Local machine | 7–8B model | Before release |

All test documents are synthetic. GitHub Actions logs are public, so real documents are never used in CI.

## Contributing

This is a public, read-only repository maintained by a single developer. Issues and pull requests are limited to collaborators. You are welcome to fork it under the MIT License.

## License

MIT
