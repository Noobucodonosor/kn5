# Deploy del bot kn5 su Google Cloud Run

Obiettivo: bot Telegram **sempre online** (anche a Mac spento). Mandi un link
Instagram/YouTube dal telefono → ricevi la conversione in testo.

## Come funziona l'auth in cloud

In cloud non c'è un browser da cui leggere i cookie di Instagram, quindi si usa
un file `cookies.txt` esportato dal Mac e caricato come **secret**. Vertex AI e
Cloud Storage usano il **service account** di Cloud Run (nessun key file).

⚠️ **Limite noto (onesto):** i cookie IG nascono dall'IP di casa; usati da un
datacenter Google, Instagram *può* invalidarli con un challenge anti-bot. Se
succede: riesporta i cookie dal Mac e ricarica il secret (vedi sotto). Con un
Mac sempre acceso questo rischio non esiste, ma perdi il "sempre online".

## Prima volta

```bash
# 1. Autenticati a Google Cloud (interattivo)
gcloud auth login

# 2. Esporta i cookie IG dal Chrome loggato su Instagram
python scripts/refresh_ig_cookies.py        # -> .secrets/instagram_cookies.txt

# 3. Deploy (crea secret, ruoli IAM, build remoto, webhook)
bash deploy/cloudrun.sh
```

Lo script chiede conferma prima di spendere. A fine deploy stampa l'URL del
servizio: manda un link al bot dal telefono per provare.

## Aggiornare il codice

Ripeti solo:

```bash
bash deploy/cloudrun.sh
```

Ridà il deploy da sorgente e riusa i secret esistenti.

## Rinfrescare i cookie IG (quando i download in cloud falliscono con 403)

```bash
python scripts/refresh_ig_cookies.py
gcloud secrets versions add ig-cookies --data-file=.secrets/instagram_cookies.txt
gcloud run services update kn5-bot --region "$GCP_LOCATION" \
  --update-env-vars "COOKIES_REFRESHED=$(date +%s)"   # forza il restart
```

## Variabili usate (da `.env`)

| Variabile | Uso |
|---|---|
| `BOT_TOKEN` | token bot Telegram → secret `bot-token` |
| `TELEGRAM_ALLOWED_USER_ID` | unico utente autorizzato + passcode dashboard |
| `GCP_PROJECT`, `GCP_LOCATION` | progetto/regione (regione = anche quella di Cloud Run) |
| `GCS_BUCKET` | bucket per upload video e report |
| `APIFY_TOKEN` | (opz.) scan profili per il bulk → secret `apify-token` |
| `CLOUDRUN_SERVICE` | (opz.) nome servizio, default `kn5-bot` |

Env e secret sono forniti a runtime da Cloud Run: `.env` e `.secrets/` **non**
vengono caricati nell'immagine (vedi `.gcloudignore`).
