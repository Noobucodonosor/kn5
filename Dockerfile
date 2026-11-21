# Dockerfile per DanyKnowledgeBot

FROM node:22-slim AS node_runtime

# Usa una versione slim ufficiale di Python
FROM python:3.11-slim

# yt-dlp usa Node per risolvere i challenge JavaScript di YouTube.
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node

# Installa ffmpeg (necessario a yt-dlp per unire video ed audio)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Imposta la directory di lavoro
WORKDIR /app

# Copia i requisiti ed installa le dipendenze
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice dell'applicazione
COPY . .

# Espone la porta del server (8080 di default per Cloud Run/Render/Railway)
EXPOSE 8080

# Avvia il bot usando gunicorn in produzione (punta alla variabile 'app' in telegram_bot.py)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "1900", "--workers", "1", "--threads", "4", "telegram_bot:app"]
