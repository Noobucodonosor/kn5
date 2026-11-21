# kn5 — scorciatoie per setup e uso. Vedi README.md per i dettagli.
PY := ./venv/bin/python
PIP := ./venv/bin/pip

.DEFAULT_GOAL := help
.PHONY: help setup check test eval repo-check verify site-check verify-all run bot cli bulk cookies deploy clean

help: ## Mostra questo aiuto
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Crea il venv, installa le dipendenze e prepara .env
	python3 -m venv venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@[ -f .env ] || (cp .env.example .env && echo ">> Creato .env — compila i valori prima di partire")
	@echo ">> Setup completato. Prossimo passo: modifica .env, poi 'make check'"

check: ## Verifica ambiente e configurazione (import + ffmpeg + config)
	@command -v ffmpeg >/dev/null && echo "[ok] ffmpeg" || echo "[!!] ffmpeg MANCANTE (brew install ffmpeg)"
	@$(PY) -c "import yt_dlp, telebot, flask, google.genai, gallery_dl; print('[ok] dipendenze Python')"
	@$(PY) -c "from src.skills.budget import BudgetTracker as B; s=B().status(); print('[ok] budget: ' + s['month'] + '  speso ' + str(s['spent_usd']) + ' / tetto ' + str(s['limit_usd']) + ' USD')"

test: ## Esegue la suite unitaria offline e deterministica
	$(PY) -m unittest discover -s tests -v

eval: ## Valuta pacchetti golden: make eval PACKAGES=/percorso/pacchetti
	@[ -n "$(PACKAGES)" ] || (echo "Uso: make eval PACKAGES=/percorso/pacchetti" && exit 1)
	$(PY) scripts/evaluate_packages.py "$(PACKAGES)"

repo-check: ## Controlla confini Git, output generati e possibili segreti
	$(PY) scripts/repo_check.py

verify: repo-check test ## Gate backend prima di commit o deploy
	$(PY) -m compileall -q src scripts tests main.py bulk_processor.py telegram_bot.py
	bash -n deploy/cloudrun.sh
	git diff --check

site-check: ## Compila il repository Sites indipendente
	npm --prefix kn5-site run build

verify-all: verify site-check ## Gate completo backend + Sites

run: bot ## Alias di 'make bot'

bot: ## Avvia il bot Telegram + dashboard web (http://localhost:8080)
	$(PY) telegram_bot.py

cli: ## Analizza un singolo URL:  make cli URL="https://..."
	@[ -n "$(URL)" ] || (echo "Uso: make cli URL=\"https://...\"" && exit 1)
	$(PY) main.py "$(URL)"

bulk: ## Bulk di un profilo/canale:  make bulk TARGET="nome" LIMIT=20
	@[ -n "$(TARGET)" ] || (echo "Uso: make bulk TARGET=\"nome_profilo\" [LIMIT=20]" && exit 1)
	$(PY) bulk_processor.py "$(TARGET)" $(if $(LIMIT),--limit $(LIMIT),)

cookies: ## Esporta i cookie Instagram dal browser (per il deploy cloud)
	$(PY) scripts/refresh_ig_cookies.py

deploy: ## Deploy del bot su Google Cloud Run (vedi deploy/README.md)
	bash deploy/cloudrun.sh

clean: ## Rimuove file temporanei e cache (NON tocca library/)
	rm -rf temp/ __pycache__ src/skills/__pycache__ scripts/__pycache__
	@echo ">> Puliti temp/ e cache. La biblioteca (library/) è intatta."
