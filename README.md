# PrivaSheet

**Privacy-first PDF-to-CSV extraction powered by local LLMs.**

PrivaSheet converts PDF documents into structured CSV files using a local LLM through [Ollama](https://ollama.com/).

Your documents stay on your machine.

```text
PDF → Extract → Local LLM → JSON → Validate → CSV
                              │
                              └── Exception → Human Review
```

## Why?

Sensitive documents can contain financial, customer, or business data that should not be uploaded to a cloud service.

PrivaSheet takes a **local-first** approach:

* **100% local processing**
* **No cloud AI required**
* **Human-in-the-loop exception handling**
* **Free and open source**
* **MIT License**

## AI-DLC

PrivaSheet is built using an **AI-Driven Development Life Cycle (AI-DLC)**.

The goal is not simply to generate code with AI, but to build AI-assisted systems with explicit engineering practices for:

* Architecture
* Quality
* Testing
* Security
* Traceability

When the system cannot confidently process a document or value, it can raise an **exception for human review instead of silently producing an unreliable result.**

## Status

**Early development**

The project is currently focused on building and validating the core extraction and exception-handling pipeline.

Production-readiness claims will be backed by tests and measurable benchmarks as the project matures.

## License

MIT
# PrivaSheet

**Privacy-first PDF-to-CSV extraction powered by local LLMs.**

PrivaSheet converts PDF documents into structured CSV files using a local LLM through [Ollama](https://ollama.com/).

Your documents stay on your machine.

```text
PDF → Extract → Local LLM → JSON → Validate → CSV
                              │
                              └── Exception → Human Review
```

## Why?

Sensitive documents can contain financial, customer, or business data that should not be uploaded to a cloud service.

PrivaSheet takes a **local-first** approach:

* **100% local processing**
* **No cloud AI required**
* **Human-in-the-loop exception handling**
* **Free and open source**
* **MIT License**

## AI-DLC

PrivaSheet is built using an **AI-Driven Development Life Cycle (AI-DLC)**.

The goal is not simply to generate code with AI, but to build AI-assisted systems with explicit engineering practices for:

* Architecture
* Quality
* Testing
* Security
* Traceability

When the system cannot confidently process a document or value, it can raise an **exception for human review instead of silently producing an unreliable result.**

## Status

**Early development**

The project is currently focused on building and validating the core extraction and exception-handling pipeline.

Production-readiness claims will be backed by tests and measurable benchmarks as the project matures.

## License

MIT
