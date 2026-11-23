# ADR-0001: Architettura Core — Ingestione Media, Pacchetti Auditable a 4 Artefatti, Sentinella Budget e Verifica Strutturale

- **Stato:** Accettato
- **Priorità:** P1 — Alta
- **Data:** 2026-11-23
- **Autori:** Dany (`Noobucodonosor`)
- **Fonti:** `README.md`, `AUTONOMY.md`, `src/skills/`, `evals/cases.json`

---

## 1. Contesto e Problema

`kn5` trasforma video YouTube e post/Reel/caroselli Instagram in conoscenza strutturata e consultabile. I rischi principali di un ingestore multimodale basato su LLM sono:
1. Mescolare sintesi elaborata dall'AI con la trascrizione letterale o il testo OCR, rendendo impossibile verificare cosa è stato effettivamente detto o mostrato.
2. Sforare il budget mensile API su esecuzioni bulk o retry automatici.
3. Dichiarare una falsa "equivalenza semantica" automatica senza revisione umana della fonte.

---

## 2. Decisione Architetturale

1. **Separazione in 4 artefatti verificabili (`LibraryWriter.write_artifact_package`):**
   - `document.md`: documento testuale adattato al genere (`tutorial`, `explanation`, `narrative`, `presentation`, `interview`).
   - `transcript.md`: trascrizione verbatim con timestamp monotoni.
   - `visual.md`: testo OCR on-screen e descrizione visiva separati dal parlato.
   - `manifest.json`: metadati del generatore, costo e contratto di verifica.
2. **Onestà epistemica nella verifica (`src/skills/verification.py`):**
   - Il gate automatico certifica `scope: "structural"` (copertura segmenti, presenza evidenze audio/visive, monotonia timestamp) e dichiara esplicitamente `semantic_equivalence: "not_verified"`.
3. **Sentinella di Budget idempotente (`src/skills/budget.py`):**
   - Controllo preventivo del tetto mensile prima di ogni chiamata Vertex AI/Gemini e registrazione idempotente tramite `event_id`.
4. **Esportazione Demo Sanitizzata (`python main.py export-demo`):**
   - Generazione deterministica di `samples.json` con 3 pacchetti reali sanitizzati e relativo `manifest` strutturale per la preview statica su GitHub Pages.

---

## 3. Conseguenze

- **Positive:** Tracciabilità completa tra fonte audio/visiva e sintesi, controllo rigoroso dei costi, suite unitaria eseguibile in <50ms completamente offline.
- **Vincoli:** La cartella `library/` e i file `.env`/cookie non devono mai essere tracciati da Git (`scripts/repo_check.py`).

---

## 4. Task Operativi Collegati

- [x] **TASK-KN5-01**: Comando `export-demo` (`samples.json`) e vista Manifest di verifica strutturale nella preview (`closes #1`)
- [x] **TASK-ADR-01**: Inizializzare `AGENTS.md`, `docs/adr/` e template Issue/PR (`closes #2`)
- [x] **TASK-CI-01**: Aggiungere workflow GitHub Actions CI (`unittest` + `adr-pre-release`) (`closes #3`)
