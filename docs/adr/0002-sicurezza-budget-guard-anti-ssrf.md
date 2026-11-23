# ADR-0002: Sicurezza, Budget Guard API e Hardening Container/Runtime (`kn5`)

- **Stato:** Accettato
- **Data:** 2026-11-23
- **Autori:** `Noobucodonosor`
- **Priorità:** `P0-critical`
- **ADR correlati:** [ADR-0001](./0001-architettura-core-kn5.md)

---

## 1. Contesto e Problema

`kn5` ingerisce URL pubblici (YouTube, Instagram, articoli) e chiama modelli multimodali Gemini / Vertex AI con potenziale esposizione a tre categorie di rischio:
1. **Sforamento incontrollato dei costi API:** elaborazioni video lunghe o batch multipli possono generare spese impreviste su Vertex AI / Gemini.
2. **Esposizione di segreti e cookie di sessione:** token Telegram, credenziali GCP e cookie Instagram non devono mai entrare nella storia Git o nelle immagini Docker.
3. **Immagini container sovradimensionate o con artefatti locali:** `COPY . .` senza `.dockerignore` rischia di includere `.git/`, `.env`, test e output locali.

---

## 2. Decisione Architetturale

1. **Sentinella Budget Obbligatoria (`BudgetTracker`):** ogni invocazione LLM passa da `BudgetTracker` con soglia mensile configurabile via variabili d'ambiente e blocco preventivo (*fail-safe*) al raggiungimento del tetto.
2. **Separazione Segreti e Configurazione:** nessun segreto nel codice sorgente; tutte le credenziali (`TELEGRAM_BOT_TOKEN`, `GCP_PROJECT`, chiavi AI) sono lette da variabili d'ambiente documentate in `.env.example`, verificate in CI da `adr-pre-release.yml`.
3. **Hardening Container & Packaging:** `.dockerignore` esclude `.git`, `.env*`, `tests/`, `evals/`, `docs/` e cache Python; `pyproject.toml` standardizza versione Python (`>=3.11`), test (`pytest`) e lint (`ruff`).
4. **Zero Branding Personale nel Codice Pubblico:** i container, i template HTML e la documentazione usano esclusivamente l'identificativo tecnico `kn5` / `Noobucodonosor`.

---

## 3. Conseguenze

- **Positive:** spesa API prevedibile e verificabile, build Docker pulito e deterministico su Cloud Run, zero fughe di credenziali o dati personali.
- **Trade-off:** i cookie per contenuti Instagram autenticati restano gestiti localmente fuori dal repository (`scripts/refresh_ig_cookies.py`).

---

## 4. Task Operativi Collegati

- [x] **TASK-SEC-01:** Hardening `.dockerignore`, `pyproject.toml`, licenza MIT e rimozione riferimenti personali (`kn5-knowledge-bot`).
- [x] **TASK-SEC-02:** Verifica pre-release automatica su CI (`.github/workflows/adr-pre-release.yml`) e suite `evals/`.
