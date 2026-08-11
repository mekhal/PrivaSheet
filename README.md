## About The Project

This project is a private, open-source (MIT License) PDF to Excel converter built to run entirely on your local machine. It solves a critical pain point in modern data processing: the privacy risk of uploading sensitive financial records, invoices, or proprietary documents to cloud-based extraction tools.

Unlike traditional rapid-prototype AI projects, this system was built and managed using a strict AI-DLC (AI-Driven Development Life Cycle) framework. It serves as a production-grade blueprint demonstrating how AI code generation can be tightly structured, verified, and architected to deliver secure and deterministic software.

### Core Features

* 100% Local Privacy: Operates entirely offline. Your documents never leave your machine, ensuring complete data security.
* Local LLM Integration: Uses open-weight models like Qwen or Llama via Ollama to handle complex table layouts that traditional rule-based parsers fail to extract.
* Structured Output: Employs a robust parsing pipeline to force deterministic JSON responses from the local model before transforming them into clean Excel sheets.
* Free and Unlimited: No paywalls, no usage tiers, and no restrictions on file size or page counts.
* AI-DLC Engineered: Fully documented with Architecture Decision Records (ADR) and integrated quality gates to prove the stability and maintainability of the codebase.
