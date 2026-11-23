# Architecture Decision Records (ADR) & Master Task Backlog — kn5

Questa cartella raccoglie gli **Architecture Decision Records (ADR)** e il **Master Task Backlog** di **kn5 — Knowledge Ingestor** (`Noobucodonosor/kn5`).

---

## Indice degli ADR

| ID | Titolo | Stato | Priorità | Autori |
| :--- | :--- | :--- | :--- | :--- |
| **[ADR-0001](./0001-architettura-core-kn5.md)** | Architettura Core — Ingestione Media, Pacchetti Auditable a 4 Artefatti, Sentinella Budget e Verifica Strutturale | Accettato | P1 — Alta | Noobucodonosor |
| **[ADR-0002](./0002-sicurezza-budget-guard-anti-ssrf.md)** | Sicurezza, Budget Guard API e Hardening Container/Runtime | Accettato | **P0 — Critica** | Noobucodonosor |

---

## Diagramma Architetturale del Sistema

```mermaid
flowchart LR
  URL["URL YouTube / Instagram"] --> FETCH["MediaFetcher\n(yt-dlp / gallery-dl)"]
  BUDGET["BudgetTracker\n(Sentinella mensile USD)"] --> SYNTH["KnowledgeSynthesizer\n(Gemini / Vertex AI)"]
  FETCH --> SYNTH
  SYNTH --> VERIFY["Structural Verification\n(scope: structural,\nsemantic: not_verified)"]
  VERIFY --> WRITER["LibraryWriter\n(document.md, transcript.md,\nvisual.md, manifest.json)"]
  WRITER --> EVAL["Gate Evals & Demo Export\n(evals/cases.json, samples.json)"]
```

---

## Master Task Backlog

| Task ID | Issue | ADR | Titolo Task | File Coinvolti | Priorità | Assegnatari |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **TASK-KN5-01** | `#1` | [ADR-0001](./0001-architettura-core-kn5.md) | Comando `export-demo` (`samples.json`) e vista Manifest di verifica strutturale nella preview | `main.py`, `tests/test_export_demo.py`, `gh-pages` | **P1 — Alta** | Noobucodonosor |
| **TASK-ADR-01** | `#2` | [ADR-0001](./0001-architettura-core-kn5.md) | Inizializzare `AGENTS.md`, `docs/adr/` e template Issue/PR | `AGENTS.md`, `docs/adr/`, `.github/` | **P1 — Alta** | Noobucodonosor |
| **TASK-CI-01** | `#3` | [ADR-0001](./0001-architettura-core-kn5.md) | Aggiungere workflow GitHub Actions CI (`unittest` + `adr-pre-release`) | `.github/workflows/ci.yml`, `.github/workflows/adr-pre-release.yml` | **P1 — Alta** | Noobucodonosor |
