"""
Bot Telegram + server Flask (webhook in prod / polling in dev).

Per ogni URL inoltrato:
1. Verifica autorizzazione utente.
2. Verifica budget mensile (BudgetTracker).
3. Scarica media + estrae metadata.
4. Manda a Gemini (KnowledgeSynthesizer).
5. Registra costo nel BudgetTracker.
6. Scrive markdown in library/ (LibraryWriter).
7. Risponde con anteprima ricca + allegato .md.
"""

import logging
import os
import re
import shutil
from pathlib import Path

import telebot
from dotenv import load_dotenv
from flask import Flask, request

_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "bot.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("telegram_bot")
logger.info("=" * 60)
logger.info("Bot avviato — log su %s", _LOG_FILE.resolve())

from src.skills.budget import BudgetExceeded, BudgetTracker
from src.skills.dashboard import dashboard_bp
from src.skills.job_api import jobs_bp
from src.skills.knowledge_synthesizer import (
    KnowledgeSynthesizer,
    KnowledgeSynthesizerError,
)
from src.skills.library_writer import LibraryWriter
from src.skills.media_fetcher import (
    DownloadError,
    InvalidURLError,
    MediaFetcher,
    VideoRemovedError,
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN non trovato nel file .env!")
if not ALLOWED_USER_ID:
    raise ValueError("TELEGRAM_ALLOWED_USER_ID non trovato nel file .env!")

try:
    ALLOWED_USER_ID = int(ALLOWED_USER_ID)
except ValueError:
    raise ValueError("TELEGRAM_ALLOWED_USER_ID deve essere un numero intero!")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "antigravity-default-secret-38914")

# Dashboard web sulla stessa app
app.register_blueprint(dashboard_bp)
app.register_blueprint(jobs_bp)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if WEBHOOK_URL:
    logger.info("Registro webhook Telegram su %s", WEBHOOK_URL)
    try:
        bot.remove_webhook()
        bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    except Exception as e:
        logger.error(f"Errore set_webhook: {e}")


@app.route("/" + BOT_TOKEN, methods=["POST"])
def get_message():
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "!", 200


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


TEMP_VIDEO = Path("temp/tg_video")
TEMP_VIDEO.mkdir(parents=True, exist_ok=True)

URL_REGEX = r"(https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*))"

# Pattern URL "pezzo singolo" (vs profilo)
SINGLE_PIECE_PATTERNS = [
    r"/reel/[A-Za-z0-9_-]+",
    r"/p/[A-Za-z0-9_-]+",
    r"/tv/[A-Za-z0-9_-]+",
    r"youtube\.com/watch",
    r"youtu\.be/",
]


def is_authorized(message):
    return message.from_user.id == ALLOWED_USER_ID


def is_single_piece(url: str) -> bool:
    return any(re.search(p, url) for p in SINGLE_PIECE_PATTERNS)


# =====================================================================
# Handlers
# =====================================================================


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    if not is_authorized(message):
        bot.reply_to(message, "Non sei autorizzato.")
        return
    bot.reply_to(
        message,
        "👋 Inoltrami un link Instagram (Reel, post, carosello) o YouTube.\n\n"
        "Lo scarico, lo analizzo con Gemini e te lo salvo nella biblioteca come "
        "markdown navigabile in Obsidian. Ti rimando un'anteprima qui in chat.\n\n"
        "Usa /budget per vedere quanto hai speso questo mese.\n"
        "Usa /status per ping di vita.",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["budget"])
def send_budget(message):
    if not is_authorized(message):
        return
    st = BudgetTracker().status()
    bar = _budget_bar(st["percent"])
    text = (
        f"💰 *Budget {st['month']}*\n"
        f"Speso: `${st['spent_usd']:.2f}` / `${st['limit_usd']:.2f}` ({st['percent']}%)\n"
        f"Rimasto: `${st['remaining_usd']:.2f}`\n"
        f"Chiamate: {st['calls']}\n"
        f"{bar}"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["status"])
def send_status(message):
    if not is_authorized(message):
        return
    bot.reply_to(message, "✅ Vivo. Inoltrami un link.")


def _budget_bar(percent: float, width: int = 18) -> str:
    filled = min(width, int(percent / 100 * width))
    return "`[" + "█" * filled + "░" * (width - filled) + f"] {percent:.0f}%`"


@bot.message_handler(func=lambda m: True)
def handle_message(message):
    if not is_authorized(message):
        bot.reply_to(message, "Non sei autorizzato.")
        return

    urls = re.findall(URL_REGEX, message.text or "")
    if not urls:
        bot.reply_to(message, "Inviami un link valido (Instagram o YouTube).")
        return

    url = urls[0]

    if not is_single_piece(url):
        bot.reply_to(
            message,
            "🔍 Sembra un profilo o un canale.\n"
            "Per processare in blocco usa il comando CLI:\n"
            "`python bulk_processor.py " + url + "`\n\n"
            "Vedrai la stima costi prima di partire.",
            parse_mode="Markdown",
        )
        return

    process_single_url(message, url)


def process_single_url(message, url: str):
    """Pipeline completa per un singolo pezzo, con progress update."""
    status_msg = bot.reply_to(message, "⏳ Ricevuto, controllo budget…")

    # 1. Budget gate
    try:
        budget = BudgetTracker()
        budget.check_or_raise()
    except BudgetExceeded as e:
        bot.edit_message_text(
            f"❌ *Budget esaurito.*\n`{str(e)[:200]}`",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="Markdown",
        )
        return

    st = budget.status()
    if st["warning"]:
        bot.send_message(
            message.chat.id,
            f"⚠️ Budget al {st['percent']}% per {st['month']}. Procedo, ma occhio.",
            parse_mode="Markdown",
        )

    # 2. Download
    h = MediaFetcher()._url_hash(url)
    session_dir = TEMP_VIDEO / h
    session_dir.mkdir(parents=True, exist_ok=True)
    fetcher = MediaFetcher(temp_dir=session_dir, cache_file=TEMP_VIDEO / "download_cache.json")

    try:
        bot.edit_message_text(
            "⬇️ Download in corso…",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
        )
        result = fetcher.fetch(url)
        media_type = result.get("media_type", "video")
        assets = result["assets"]

        bot.edit_message_text(
            f"📎 Estrazione metadata ({media_type}, {len(assets)} asset)…",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
        )
        metadata = fetcher.extract_metadata(url)
        metadata["media_type"] = media_type

        # 3. Gemini
        bot.edit_message_text(
            "🧠 Analisi Gemini…",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
        )
        synthesizer = KnowledgeSynthesizer()
        if media_type == "video":
            pm = synthesizer.generate_from_video(assets[0])
        else:
            pm = synthesizer.generate_from_images(assets)

        # 4. Budget record
        usage = pm.pop("_usage", {"input_tokens": 0, "output_tokens": 0})
        label = pm.get("title") or metadata.get("title") or metadata.get("shortcode") or url[:60]
        cost = budget.record(
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            model=synthesizer.model_name,
            label=label,
        )

        # 5. Library write
        bot.edit_message_text(
            "📚 Salvataggio in biblioteca…",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
        )
        writer = LibraryWriter()
        md_path = writer.write_piece(pm, metadata, cost_usd=cost)

        # 6. Risposta ricca + allegato
        preview = build_preview(pm, metadata, cost, budget.status())

        with open(md_path, "rb") as f:
            bot.send_document(
                chat_id=message.chat.id,
                document=f,
                caption=preview,
                parse_mode="Markdown",
            )
        bot.delete_message(chat_id=message.chat.id, message_id=status_msg.message_id)

    except KnowledgeSynthesizerError as e:
        logger.error("Synthesizer error: %s", e, exc_info=True)
        bot.edit_message_text(
            f"❌ Errore Gemini: `{str(e)[:200]}`",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="Markdown",
        )
    except (InvalidURLError, VideoRemovedError, DownloadError) as e:
        bot.edit_message_text(
            f"❌ Errore download: `{str(e)[:200]}`",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Errore processing %s: %s", url, e, exc_info=True)
        bot.edit_message_text(
            f"❌ Errore inatteso: `{str(e)[:200]}`",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="Markdown",
        )
    finally:
        try:
            if session_dir.exists():
                shutil.rmtree(session_dir)
        except Exception as e:
            logger.warning("Cleanup sessione %s fallito: %s", h, e)


def build_preview(pm: dict, metadata: dict, cost: float, budget_status: dict) -> str:
    """Anteprima Markdown da mandare in caption del documento Telegram."""
    title = pm.get("title") or metadata.get("title") or "(senza titolo)"
    summary = (pm.get("executive_summary") or "").strip()
    if len(summary) > 350:
        summary = summary[:347] + "…"

    steps = pm.get("steps") or []
    steps_lines = []
    for s in steps[:4]:
        t = s.get("title", "")
        n = s.get("step_number", "?")
        steps_lines.append(f"  {n}. {t}")
    if len(steps) > 4:
        steps_lines.append(f"  …e altri {len(steps) - 4}")

    tags = pm.get("tags") or []
    tag_str = " ".join(f"#{t.replace('-', '_')}" for t in tags[:6])

    profile = metadata.get("profile")
    profile_line = f"👤 @{profile}\n" if profile else ""

    lines = [
        f"✅ *{_escape_md(title)}*",
        "",
        profile_line + (f"📝 _{_escape_md(summary)}_" if summary else "_(nessuna sintesi)_"),
    ]
    if steps_lines:
        lines += ["", "🔑 *Passi:*", *[_escape_md(s) for s in steps_lines]]
    if tag_str:
        lines += ["", _escape_md(tag_str)]
    lines += [
        "",
        f"💸 ${cost:.4f}  •  budget {budget_status['percent']:.0f}%  "
        f"(${budget_status['spent_usd']:.2f}/${budget_status['limit_usd']:.2f})",
    ]
    return "\n".join(lines)


def _escape_md(text: str) -> str:
    """Escape minimo per Markdown legacy di Telegram."""
    if not text:
        return ""
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


if __name__ == "__main__":
    PORT = int(os.getenv("PORT", 8080))
    if WEBHOOK_URL:
        logger.info("Webhook server su porta %s", PORT)
        app.run(host="0.0.0.0", port=PORT)
    else:
        # In modalità locale: polling per il bot in un thread daemon,
        # Flask in main thread così la dashboard/biblioteca/budget restano accessibili
        # da browser su http://localhost:<PORT>.
        import threading
        logger.info("Polling locale + Flask su porta %s (dashboard / biblioteca / budget)", PORT)
        bot.remove_webhook()

        import time

        def _polling_loop():
            # infinity_polling gestisce già i retry interni, ma un ReadTimeout
            # verso api.telegram.org può comunque farlo terminare: qui lo
            # riavviamo all'infinito con backoff così il bot non muore mai in
            # silenzio (è la causa del crash del 2026-06-20).
            while True:
                try:
                    bot.infinity_polling(
                        timeout=30,
                        long_polling_timeout=30,
                        logger_level=logging.ERROR,
                    )
                    logger.warning("infinity_polling terminato, riavvio tra 5s…")
                except Exception as e:
                    logger.error("Polling crashato, riavvio tra 5s: %s", e, exc_info=True)
                time.sleep(5)

        t = threading.Thread(target=_polling_loop, daemon=True, name="tg-polling")
        t.start()
        # Flask in foreground così Ctrl+C ferma il processo intero
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
