#!/usr/bin/env bash
#
# Deploy del bot kn5 su Google Cloud Run (sempre online, anche a Mac spento).
#
# Cosa fa:
#   1. Crea/aggiorna i secret (BOT_TOKEN, cookie IG, APIFY_TOKEN) in Secret Manager.
#   2. Concede al service account di Cloud Run i ruoli Vertex AI + Storage.
#   3. Fa il deploy da sorgente (build remoto: NON serve Docker locale).
#   4. Configura il webhook Telegram puntando all'URL del servizio (2 passi).
#
# Prerequisiti (una volta):
#   - gcloud autenticato:            gcloud auth login
#   - Cookie IG esportati:           python scripts/refresh_ig_cookies.py
#
# Uso:   bash deploy/cloudrun.sh        (AUTO_YES=1 per saltare la conferma)
#
set -euo pipefail
cd "$(dirname "$0")/.."

# --- Config (da .env, override via ambiente) --------------------------------
set -a; [ -f .env ] && . ./.env; set +a
SERVICE="${CLOUDRUN_SERVICE:-kn5-bot}"
REGION="${GCP_LOCATION:-europe-west1}"
PROJECT="${GCP_PROJECT:?GCP_PROJECT mancante in .env}"
COOKIES_FILE="${LOCAL_IG_COOKIES:-.secrets/instagram_cookies.txt}"
YT_COOKIES_FILE="${LOCAL_YOUTUBE_COOKIES:-.secrets/youtube_cookies.txt}"

echo "----------------------------------------------"
echo " Progetto : $PROJECT"
echo " Servizio : $SERVICE   Regione: $REGION"
echo " Bucket   : ${GCS_BUCKET:-<non settato>}"
echo "----------------------------------------------"
if [ "${AUTO_YES:-0}" != "1" ]; then
  read -r -p "Procedo col deploy su Cloud Run? Comporta costi GCP. [y/N] " ok
  [ "$ok" = "y" ] || { echo "Annullato."; exit 0; }
fi

gcloud config set project "$PROJECT" >/dev/null

echo ">> Abilito le API necessarie..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
    secretmanager.googleapis.com aiplatform.googleapis.com cloudtasks.googleapis.com >/dev/null

# --- Secret Manager ----------------------------------------------------------
upsert_secret () {   # $1 = nome secret, $2 = file con il valore
  local name="$1" file="$2"
  if ! gcloud secrets describe "$name" >/dev/null 2>&1; then
    gcloud secrets create "$name" --replication-policy=automatic >/dev/null
  fi
  gcloud secrets versions add "$name" --data-file="$file" >/dev/null
  echo "  [ok] secret $name aggiornato"
}

ensure_random_secret () {  # crea una sola versione e poi la conserva
  local name="$1"
  if ! gcloud secrets describe "$name" >/dev/null 2>&1; then
    gcloud secrets create "$name" --replication-policy=automatic >/dev/null
    printf '%s' "$(openssl rand -hex 32)" | gcloud secrets versions add "$name" --data-file=- >/dev/null
    echo "  [ok] secret $name creato"
  else
    echo "  [ok] secret $name già presente"
  fi
}

echo ">> Carico i secret..."
[ -f "$COOKIES_FILE" ] || { echo "[x] $COOKIES_FILE assente. Lancia: python scripts/refresh_ig_cookies.py"; exit 1; }
upsert_secret ig-cookies "$COOKIES_FILE"
[ -f "$YT_COOKIES_FILE" ] && upsert_secret youtube-cookies "$YT_COOKIES_FILE"
printf '%s' "${BOT_TOKEN:?BOT_TOKEN mancante in .env}" | upsert_secret bot-token /dev/stdin
if [ -n "${APIFY_TOKEN:-}" ]; then
  printf '%s' "$APIFY_TOKEN" | upsert_secret apify-token /dev/stdin
fi
ensure_random_secret kn5-api-token
ensure_random_secret kn5-worker-token

# --- Ruoli IAM per il service account di runtime -----------------------------
PROJNUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUNTIME_SA="${PROJNUM}-compute@developer.gserviceaccount.com"
echo ">> Concedo i ruoli a ${RUNTIME_SA} ..."
for role in roles/aiplatform.user roles/storage.objectAdmin roles/secretmanager.secretAccessor roles/cloudtasks.enqueuer; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${RUNTIME_SA}" --role="$role" \
    --condition=None >/dev/null
done

TASK_QUEUE="${CLOUD_TASKS_QUEUE:-kn5-jobs}"
if ! gcloud tasks queues describe "$TASK_QUEUE" --location "$REGION" >/dev/null 2>&1; then
  gcloud tasks queues create "$TASK_QUEUE" --location "$REGION" \
    --max-concurrent-dispatches=1 --max-dispatches-per-second=1 >/dev/null
fi
gcloud tasks queues update "$TASK_QUEUE" --location "$REGION" \
  --max-concurrent-dispatches=1 --max-dispatches-per-second=1 \
  --max-attempts=5 --min-backoff=10s --max-backoff=300s --max-doublings=4 >/dev/null

# --- Deploy (passo 1: senza webhook, per ottenere l'URL) ---------------------
ENV_VARS="TELEGRAM_ALLOWED_USER_ID=${TELEGRAM_ALLOWED_USER_ID},GCP_PROJECT=${PROJECT},GCP_LOCATION=${REGION},GCS_BUCKET=${GCS_BUCKET},INSTAGRAM_COOKIES_FILE=/secrets/ig/instagram_cookies.txt,YOUTUBE_COOKIES_FILE=/secrets/yt/youtube_cookies.txt,INSTAGRAM_BROWSER=,FLASK_SECRET_KEY=${FLASK_SECRET_KEY:-kn5-cloud-secret},CLOUD_TASKS_QUEUE=${TASK_QUEUE},CLOUD_TASKS_LOCATION=${REGION},LONG_VIDEO_SEGMENT_SEC=${LONG_VIDEO_SEGMENT_SEC:-900},GEMINI_MAX_ATTEMPTS=${GEMINI_MAX_ATTEMPTS:-4},GEMINI_RETRY_BASE_SECONDS=${GEMINI_RETRY_BASE_SECONDS:-2}"
[ -n "${DASHBOARD_PASSWORD:-}" ] && ENV_VARS="${ENV_VARS},DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD}"

SECRETS="/secrets/ig/instagram_cookies.txt=ig-cookies:latest,BOT_TOKEN=bot-token:latest,KN5_API_TOKEN=kn5-api-token:latest,JOB_WORKER_TOKEN=kn5-worker-token:latest"
[ -f "$YT_COOKIES_FILE" ] && SECRETS="${SECRETS},/secrets/yt/youtube_cookies.txt=youtube-cookies:latest"
[ -n "${APIFY_TOKEN:-}" ] && SECRETS="${SECRETS},APIFY_TOKEN=apify-token:latest"

echo ">> Deploy da sorgente (build remoto)..."
gcloud run deploy "$SERVICE" \
  --source . --region "$REGION" \
  --allow-unauthenticated \
  --memory 2Gi --cpu 2 --timeout 1800 --concurrency 4 \
  --set-env-vars "$ENV_VARS" \
  --set-secrets "$SECRETS"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo ">> Servizio online: $URL"

# --- Deploy (passo 2: setta WEBHOOK_URL cosi il bot registra il webhook) ------
echo ">> Configuro il webhook Telegram..."
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "WEBHOOK_URL=${URL}" >/dev/null

echo "----------------------------------------------"
echo " [OK] Deploy completato."
echo "      Bot online 24/7 su: $URL"
echo "      Dashboard/biblioteca: ${URL}/  (passcode: il tuo Telegram user ID)"
echo "      Manda un link IG al bot dal telefono per provare."
echo "----------------------------------------------"
