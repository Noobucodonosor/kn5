# AGENTS.md — Contesto & Linee Guida per Agenti AI e Sviluppatori (`kn5`)

> **REGOLA AUREA (MANDATORY):**
> Prima di modificare l'architettura, il formato dei pacchetti (`document.md`, `transcript.md`, `visual.md`, `manifest.json`) o il budget tracker, **DEVI leggere [`docs/adr/README.md`](./docs/adr/README.md)** e gli ADR specifici nella cartella [`docs/adr/`](./docs/adr/).

---

## 1. Panoramica del Progetto

- **Nome Progetto:** `kn5 — Knowledge Ingestor`
- **Repository:** `Noobucodonosor/kn5`
- **Vetrina Noobucodonosor:** Servizio AI prescaricato (`https://noobucodonosor-hub-664856461640.us-central1.run.app/?app=kn5`, profilo `long-rag`) + Anteprima GitHub Pages (`https://noobucodonosor.github.io/kn5/`).
- **Stack Tecnologico:**
  - **Runtime:** Python 3.11+ (`yt-dlp`, `gallery-dl`, `google-genai` / Vertex AI, Flask, `pyTelegramBotAPI`)
  - **Storage & Output:** Pacchetti Markdown + JSON verificabili in `library/profiles/<profilo>/` e checkpoint su Google Cloud Storage
  - **QA & Verifica:** `unittest` (`tests/`), `scripts/evaluate_packages.py` (`evals/cases.json`), `scripts/repo_check.py`

---

## 2. Mappa dei File Principali

| File / Cartella | Ruolo |
| :--- | :--- |
| [`docs/adr/README.md`](./docs/adr/README.md) | Indice degli ADR, diagramma architetturale e Master Task Backlog. |
| [`main.py`](./main.py) | Orchestratore CLI per singolo URL e comando `python main.py export-demo`. |
| [`bulk_processor.py`](./bulk_processor.py) | Elaborazione batch di profili/canali con filtro duplicati. |
| [`telegram_bot.py`](./telegram_bot.py) | Bot Telegram + dashboard web Flask (`http://localhost:8080`). |
| [`src/skills/`](./src/skills/) | Moduli core: `media_fetcher.py`, `knowledge_synthesizer.py`, `library_writer.py`, `budget.py`, `content_schema.py`, `verification.py`, `job_store.py`. |
| [`evals/cases.json`](./evals/cases.json) | Casi golden per la verifica automatica dei pacchetti estratti. |
| [`scripts/repo_check.py`](./scripts/repo_check.py) | Gate di sicurezza su confini Git, file generati e scansione segreti. |
| [`tests/`](./tests/) | Suite di test unitari offline (`python3 -m unittest discover -s tests -v`). |

---

## 3. Comandi Rapidi di Sviluppo e Test

```bash
# Suite di test unitari offline
python3 -m unittest discover -s tests -v

# Gate confini repository e secret scan
python3 scripts/repo_check.py

# Esportazione pacchetti demo sanitizzati (samples.json)
python3 main.py export-demo --out samples.json

# Verifica pre-release ADR e sicurezza
python3 ~/.gemini/config/skills/adr-github-methodology/scripts/pre_release_check.py .
```
