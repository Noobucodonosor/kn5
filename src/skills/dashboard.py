import os
import re
import json
import time
import shutil
import logging
from pathlib import Path
from flask import Blueprint, request, jsonify, render_template_string, Response, session, redirect, url_for
from google.cloud import storage
import yt_dlp

# Carica i moduli interni
from src.skills.budget import BudgetTracker
from src.skills.library_browser import LibraryBrowser
from src.skills.media_fetcher import MediaFetcher
from src.skills.knowledge_synthesizer import KnowledgeSynthesizer
from src.skills.html_generator import generate_epistemic_html

logger = logging.getLogger("dashboard")

dashboard_bp = Blueprint("dashboard", __name__)

# Cartella temporanea locale per il processing web
TEMP_WEB_DIR = Path("temp/web_processing")
TEMP_WEB_DIR.mkdir(parents=True, exist_ok=True)

# Tariffe stimate Vertex AI (Gemini 2.5 Flash)
GEMINI_INPUT_USD_PER_1M = 0.075
GEMINI_OUTPUT_USD_PER_1M = 0.30
TOKENS_PER_VIDEO_SECOND = 260
AVERAGE_OUTPUT_TOKENS = 2500

def get_storage_bucket():
    """Ritorna il bucket GCS configurato."""
    project_id = os.getenv("GCP_PROJECT", "").strip()
    bucket_name = os.getenv("GCS_BUCKET", "").strip()
    if not project_id or not bucket_name:
        logger.error("GCP_PROJECT / GCS_BUCKET non configurati nel .env — bucket GCS non disponibile")
        return None
    try:
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if creds_path and not os.path.exists(creds_path):
            del os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
            
        storage_client = storage.Client(project=project_id)
        return storage_client.bucket(bucket_name)
    except Exception as e:
        logger.error(f"Errore inizializzazione Storage Client: {e}")
        return None

def is_authenticated():
    """Controlla se l'utente è autenticato tramite sessione."""
    if session.get("authenticated") is True:
        return True
    # Se non è impostata una password/passcode, consentiamo l'accesso
    allowed_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    db_pass = os.getenv("DASHBOARD_PASSWORD")
    if not allowed_id and not db_pass:
        return True
    return False

# =====================================================================
# API Endpoints
# =====================================================================

@dashboard_bp.route("/api/auth", methods=["POST"])
def auth():
    """Verifica il passcode dell'utente."""
    data = request.json or {}
    passcode = str(data.get("passcode", "")).strip()
    
    allowed_id = str(os.getenv("TELEGRAM_ALLOWED_USER_ID", "")).strip()
    db_pass = str(os.getenv("DASHBOARD_PASSWORD", "")).strip()
    
    if passcode and (passcode == allowed_id or passcode == db_pass):
        session["authenticated"] = True
        return jsonify({"success": True})
        
    return jsonify({"success": False, "error": "Passcode errato"}), 401

@dashboard_bp.route("/api/logout", methods=["POST"])
def logout():
    session.pop("authenticated", None)
    return jsonify({"success": True})


# =====================================================================
# Budget API + widget standalone
# =====================================================================

@dashboard_bp.route("/api/budget", methods=["GET"])
def api_budget():
    """Stato budget + ultimi eventi del mese corrente."""
    if not is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    tracker = BudgetTracker()
    return jsonify({
        "status": tracker.status(),
        "events": tracker.history(last_n_events=15),
    })


@dashboard_bp.route("/budget", methods=["GET"])
def budget_widget():
    """Pagina standalone per monitorare il budget — bookmarkabile, iframe-friendly."""
    return render_template_string(BUDGET_WIDGET_HTML)


# =====================================================================
# Library browser API + UI
# =====================================================================

@dashboard_bp.route("/api/library", methods=["GET"])
def api_library_profiles():
    if not is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    browser = LibraryBrowser()
    return jsonify({"profiles": browser.list_profiles()})


@dashboard_bp.route("/api/library/<profile_slug>", methods=["GET"])
def api_library_profile(profile_slug):
    if not is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    browser = LibraryBrowser()
    pieces = browser.list_pieces(profile_slug)
    return jsonify({"profile": profile_slug, "pieces": pieces})


@dashboard_bp.route("/api/library/<profile_slug>/<path:filename>", methods=["GET"])
def api_library_piece(profile_slug, filename):
    if not is_authenticated():
        return jsonify({"error": "unauthorized"}), 401
    browser = LibraryBrowser()
    piece = browser.read_piece(profile_slug, filename)
    if not piece:
        return jsonify({"error": "not found"}), 404
    return jsonify(piece)


@dashboard_bp.route("/library", methods=["GET"])
def library_ui():
    """SPA per navigare la biblioteca."""
    return render_template_string(LIBRARY_HTML)


BUDGET_WIDGET_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Budget Gemini — kn5</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body { background:#090d16; color:#f8fafc; font-family:'Inter',sans-serif; }
  .glow-warn { box-shadow: 0 0 24px rgba(245,158,11,0.35); }
  .glow-bad  { box-shadow: 0 0 24px rgba(239,68,68,0.45); }
</style>
</head>
<body class="min-h-screen p-4 md:p-8">
<div class="max-w-2xl mx-auto">
  <header class="mb-6">
    <span class="text-xs uppercase tracking-widest text-purple-400 font-semibold">Spesa Vertex AI</span>
    <h1 class="text-2xl font-bold text-white mt-0.5">Budget Gemini — <span id="month">…</span></h1>
  </header>

  <div id="card" class="bg-slate-900/50 border border-slate-800 rounded-2xl p-6 mb-6">
    <div class="flex items-baseline justify-between mb-4">
      <div>
        <div class="text-3xl font-extrabold text-white" id="spent">$0.00</div>
        <div class="text-sm text-slate-400">su <span id="limit">$0</span> di limite mensile</div>
      </div>
      <div class="text-right">
        <div class="text-2xl font-bold text-purple-300" id="percent">0%</div>
        <div class="text-xs text-slate-500">utilizzo</div>
      </div>
    </div>

    <div class="w-full h-3 bg-slate-800 rounded-full overflow-hidden mb-3">
      <div id="bar" class="h-full bg-gradient-to-r from-purple-500 to-indigo-500 transition-all duration-500" style="width:0%"></div>
    </div>

    <div class="flex justify-between text-sm text-slate-400">
      <div>Rimasto: <span class="text-slate-200 font-semibold" id="remaining">$0.00</span></div>
      <div>Chiamate: <span class="text-slate-200 font-semibold" id="calls">0</span></div>
    </div>
  </div>

  <div id="alert" class="hidden mb-6 p-4 rounded-xl border text-sm"></div>

  <h2 class="text-lg font-bold text-white mb-3">Ultime chiamate</h2>
  <div id="events" class="space-y-2"></div>

  <p class="text-xs text-slate-500 mt-8">
    Stima basata su input/output tokens × prezzo unitario configurato in <code>GEMINI_INPUT_USD_PER_1M</code> /
    <code>GEMINI_OUTPUT_USD_PER_1M</code>. La fattura GCP reale arriva con 24–48h di ritardo.
  </p>
</div>

<script>
async function refresh() {
  try {
    const r = await fetch('/api/budget');
    if (!r.ok) {
      document.getElementById('alert').classList.remove('hidden');
      document.getElementById('alert').className = 'mb-6 p-4 rounded-xl border border-red-500/40 bg-red-950/30 text-red-300';
      document.getElementById('alert').textContent = 'Non autorizzato. Apri prima la dashboard principale e logga.';
      return;
    }
    const data = await r.json();
    const s = data.status;

    document.getElementById('month').textContent = s.month;
    document.getElementById('spent').textContent = '$' + s.spent_usd.toFixed(2);
    document.getElementById('limit').textContent = '$' + s.limit_usd.toFixed(2);
    document.getElementById('percent').textContent = s.percent.toFixed(0) + '%';
    document.getElementById('remaining').textContent = '$' + s.remaining_usd.toFixed(2);
    document.getElementById('calls').textContent = s.calls;
    document.getElementById('bar').style.width = Math.min(100, s.percent) + '%';

    const card = document.getElementById('card');
    card.classList.remove('glow-warn', 'glow-bad', 'border-amber-500/40', 'border-red-500/40');
    const alertEl = document.getElementById('alert');
    alertEl.classList.add('hidden');

    if (s.exceeded) {
      card.classList.add('border-red-500/40', 'glow-bad');
      document.getElementById('bar').className = 'h-full bg-gradient-to-r from-red-500 to-rose-500 transition-all duration-500';
      alertEl.classList.remove('hidden');
      alertEl.className = 'mb-6 p-4 rounded-xl border border-red-500/40 bg-red-950/30 text-red-300';
      alertEl.textContent = '🚫 Budget mensile superato. Le nuove chiamate Gemini sono bloccate fino al prossimo mese (o alza MONTHLY_BUDGET_USD).';
    } else if (s.warning) {
      card.classList.add('border-amber-500/40', 'glow-warn');
      document.getElementById('bar').className = 'h-full bg-gradient-to-r from-amber-500 to-orange-500 transition-all duration-500';
      alertEl.classList.remove('hidden');
      alertEl.className = 'mb-6 p-4 rounded-xl border border-amber-500/40 bg-amber-950/30 text-amber-300';
      alertEl.textContent = '⚠️ Hai usato oltre l\\'80% del budget mensile. Riduci il ritmo o alza il cap.';
    }

    const evRoot = document.getElementById('events');
    evRoot.innerHTML = '';
    (data.events || []).forEach(ev => {
      const div = document.createElement('div');
      div.className = 'flex items-center justify-between bg-slate-900/40 border border-slate-800/60 rounded-lg px-4 py-2 text-sm';
      const label = ev.label || ev.model;
      div.innerHTML = `
        <div class="flex-1 min-w-0 mr-3">
          <div class="text-slate-200 truncate">${label}</div>
          <div class="text-xs text-slate-500">${ev.ts}  ·  ${ev.in.toLocaleString()} in / ${ev.out.toLocaleString()} out</div>
        </div>
        <div class="text-slate-100 font-mono">$${ev.usd.toFixed(4)}</div>
      `;
      evRoot.appendChild(div);
    });
  } catch(e) {
    console.error(e);
  }
}
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""


LIBRARY_HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Biblioteca — kn5</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;600;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root { color-scheme: dark; }
  body { font-family:'Inter',sans-serif; background:#070a12; color:#f8fafc; }
  h1,h2,h3,h4,.font-outfit { font-family:'Outfit',sans-serif; }
  .font-mono, code, pre { font-family:'JetBrains Mono',ui-monospace,monospace; }
  ::-webkit-scrollbar { width:8px; height:8px; }
  ::-webkit-scrollbar-track { background:#070a12; }
  ::-webkit-scrollbar-thumb { background:#1e293b; border-radius:4px; }

  /* Markdown rendering */
  .md h1 { font-size: 1.75rem; font-weight: 800; color:#fff; margin: 1.5rem 0 1rem; padding-bottom: .5rem; border-bottom: 1px solid rgba(148,163,184,.15); }
  .md h2 { font-size: 1.25rem; font-weight: 700; color:#e9d5ff; margin: 2rem 0 .75rem; }
  .md h3 { font-size: 1.05rem; font-weight: 700; color:#cbd5e1; margin: 1.5rem 0 .5rem; }
  .md p  { margin: .65rem 0; line-height: 1.7; color:#e2e8f0; }
  .md ul, .md ol { margin: .5rem 0 .75rem 1.25rem; }
  .md li { margin: .25rem 0; line-height: 1.65; color:#e2e8f0; }
  .md strong { color:#fff; }
  .md em { color:#cbd5e1; }
  .md blockquote { border-left: 3px solid #a855f7; padding-left: 1rem; margin: 1rem 0; color:#cbd5e1; font-style: italic; background: rgba(168,85,247,0.04); border-radius: 0 .5rem .5rem 0; padding: .75rem 1rem; }
  .md code { background:#1e293b; padding: .15rem .4rem; border-radius: .25rem; font-size: .9em; color:#fbbf24; }
  .md pre { background:#0f172a; border:1px solid #1e293b; border-radius: .5rem; padding: 1rem; overflow-x: auto; margin: 1rem 0; }
  .md pre code { background: transparent; padding: 0; color:#e2e8f0; }
  .md hr { border: none; border-top: 1px solid rgba(148,163,184,.15); margin: 2rem 0; }
  .md a { color:#c4b5fd; text-decoration: underline; text-underline-offset: 2px; }
  .md a:hover { color:#e9d5ff; }

  .piece-link { transition: all .15s; }
  .piece-link.active { background: linear-gradient(135deg, rgba(168,85,247,0.18), rgba(99,102,241,0.08)); border-color: rgba(168,85,247,0.4); }
  .profile-link.active { background: rgba(168,85,247,0.12); color: #e9d5ff; border-left: 3px solid #a855f7; }
</style>
</head>
<body class="min-h-screen">

<!-- Header sticky -->
<header class="border-b border-slate-800/80 bg-slate-950/95 backdrop-blur-md sticky top-0 z-40">
  <div class="px-4 md:px-6 py-3 flex items-center gap-4">
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-3">
        <span class="text-2xl">📚</span>
        <div>
          <span class="text-[10px] uppercase tracking-widest text-purple-400 font-semibold">kn5</span>
          <h1 class="text-lg md:text-xl font-extrabold text-white leading-none mt-0.5">Biblioteca</h1>
        </div>
      </div>
    </div>

    <!-- Mini budget -->
    <a href="/budget" target="_blank" id="budget-mini"
       class="hidden md:flex items-center gap-3 px-3 py-2 rounded-lg border border-slate-800 hover:border-slate-700 transition group">
      <div class="text-right">
        <div class="text-[10px] uppercase tracking-wider text-slate-500 group-hover:text-slate-400">Budget mese</div>
        <div class="text-xs font-mono text-slate-200" id="mini-text">…</div>
      </div>
      <div class="w-16 h-1.5 bg-slate-800 rounded-full overflow-hidden">
        <div id="mini-bar" class="h-full bg-gradient-to-r from-purple-500 to-indigo-500 transition-all" style="width:0%"></div>
      </div>
    </a>

    <a href="/" class="text-xs text-slate-500 hover:text-slate-300 transition">Dashboard →</a>
  </div>
</header>

<div class="flex flex-col md:flex-row">

  <!-- Sidebar profili -->
  <aside class="md:w-72 md:border-r border-slate-800/80 md:min-h-[calc(100vh-58px)] md:sticky md:top-[58px] md:max-h-[calc(100vh-58px)] md:overflow-y-auto">
    <div class="p-4 border-b border-slate-800/60">
      <div class="text-[10px] uppercase tracking-widest text-slate-500 font-semibold mb-2">Profili</div>
      <div id="profile-count" class="text-xs text-slate-400">caricamento…</div>
    </div>
    <nav id="profiles" class="py-2"></nav>
  </aside>

  <!-- Main -->
  <main class="flex-1 min-w-0 p-4 md:p-8">

    <!-- Vista 1: nessuna selezione (home) -->
    <section id="view-home" class="">
      <div class="max-w-2xl mx-auto text-center py-12">
        <div class="text-6xl mb-6">📖</div>
        <h2 class="text-2xl font-bold text-white mb-3">La tua biblioteca personale</h2>
        <p class="text-slate-400 leading-relaxed mb-8">
          Ogni Reel, post o video che inoltri al bot Telegram viene salvato qui come
          markdown denso con trascrizione, OCR e sintesi. Scegli un profilo dalla
          sidebar per esplorarlo, o usa <code class="text-purple-300">bulk_processor.py</code>
          per scansionare un profilo intero.
        </p>
        <div id="recent-card" class="hidden text-left bg-slate-900/40 border border-slate-800 rounded-2xl p-5">
          <div class="text-[10px] uppercase tracking-wider text-slate-500 mb-2">Ultimo pezzo</div>
          <div id="recent-content"></div>
        </div>
      </div>
    </section>

    <!-- Vista 2: lista pezzi di un profilo -->
    <section id="view-profile" class="hidden">
      <div class="flex items-baseline justify-between mb-6 border-b border-slate-800/60 pb-4">
        <div>
          <div class="text-[10px] uppercase tracking-widest text-purple-400 font-semibold">Profilo</div>
          <h2 class="text-2xl md:text-3xl font-extrabold text-white" id="profile-title">…</h2>
          <div class="text-sm text-slate-500 mt-1" id="profile-meta">…</div>
        </div>
      </div>
      <div id="pieces-list" class="space-y-3"></div>
    </section>

    <!-- Vista 3: lettura di un pezzo -->
    <section id="view-piece" class="hidden">
      <button onclick="goBack()" class="mb-6 text-sm text-slate-400 hover:text-slate-200 transition flex items-center gap-2">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
        </svg>
        <span id="back-label">indietro</span>
      </button>

      <!-- Metadata card -->
      <div id="piece-meta" class="mb-8 bg-gradient-to-br from-slate-900/60 to-slate-950/60 border border-slate-800/80 rounded-2xl p-5"></div>

      <!-- Markdown rendered -->
      <article id="piece-body" class="md max-w-3xl"></article>

      <div class="mt-12 pt-6 border-t border-slate-800/60 text-xs text-slate-500 flex flex-wrap gap-4">
        <a id="piece-source" target="_blank" class="hover:text-purple-300 transition">Apri sorgente originale ↗</a>
        <span id="piece-filename" class="font-mono"></span>
      </div>
    </section>

  </main>
</div>

<script>
// =====================================================================
// Utility
// =====================================================================

const $ = (id) => document.getElementById(id);
let STATE = { profiles: [], currentProfile: null, currentPiece: null };

function showView(name) {
  ['home', 'profile', 'piece'].forEach(v => {
    $(`view-${v}`).classList.toggle('hidden', v !== name);
  });
}

function fmt(n) {
  if (n == null) return '';
  if (n >= 1_000_000) return (n/1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n/1_000).toFixed(1) + 'k';
  return String(n);
}

function escapeHtml(s) {
  return (s ?? '').toString()
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// =====================================================================
// API
// =====================================================================

async function api(path) {
  const r = await fetch(path);
  if (r.status === 401) {
    document.body.innerHTML = '<div class="p-12 text-center"><h1 class="text-2xl font-bold text-amber-400 mb-4">🔒 Non autenticato</h1><p class="text-slate-400">Apri prima <a href="/" class="text-purple-300 underline">la dashboard</a> e logga, poi torna qui.</p></div>';
    throw new Error('unauthorized');
  }
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

// =====================================================================
// Render
// =====================================================================

async function loadProfiles() {
  const data = await api('/api/library');
  STATE.profiles = data.profiles || [];
  const total = STATE.profiles.reduce((s, p) => s + p.piece_count, 0);
  $('profile-count').textContent = `${STATE.profiles.length} profili · ${total} pezzi totali`;

  const nav = $('profiles');
  nav.innerHTML = '';
  if (!STATE.profiles.length) {
    nav.innerHTML = '<div class="px-4 py-6 text-sm text-slate-500">Biblioteca vuota. Inoltra il primo link al bot.</div>';
    return;
  }
  STATE.profiles.forEach(p => {
    const a = document.createElement('a');
    a.href = `#/${encodeURIComponent(p.slug)}`;
    a.className = 'profile-link block px-4 py-3 hover:bg-slate-900/60 border-l-3 border-transparent';
    a.innerHTML = `
      <div class="flex items-center justify-between gap-2 mb-0.5">
        <span class="font-semibold text-slate-200 truncate">${escapeHtml(p.name)}</span>
        <span class="text-[10px] font-mono text-slate-500 shrink-0">${p.piece_count}</span>
      </div>
      <div class="text-xs text-slate-500 truncate">${escapeHtml(p.last_title || '—')}</div>
      ${p.last_update ? `<div class="text-[10px] text-slate-600 mt-0.5">${p.last_update}</div>` : ''}
    `;
    a.dataset.slug = p.slug;
    nav.appendChild(a);
  });

  // Recent card on home
  if (STATE.profiles[0]) {
    const r = STATE.profiles[0];
    $('recent-card').classList.remove('hidden');
    $('recent-content').innerHTML = `
      <a href="#/${encodeURIComponent(r.slug)}" class="block group">
        <div class="font-bold text-white group-hover:text-purple-200 transition mb-1">${escapeHtml(r.last_title || '(senza titolo)')}</div>
        <div class="text-sm text-slate-400">@${escapeHtml(r.name)} · ${r.last_update || ''}</div>
      </a>
    `;
  }
}

async function loadProfile(slug) {
  const profile = STATE.profiles.find(p => p.slug === slug);
  if (!profile) { console.warn('profile not in cache', slug); }

  $('profile-title').textContent = profile ? profile.name : slug;
  $('profile-meta').textContent = profile ? `${profile.piece_count} pezzi · ultimo aggiornamento ${profile.last_update || '—'}` : '';

  const data = await api(`/api/library/${encodeURIComponent(slug)}`);
  const list = $('pieces-list');
  list.innerHTML = '';
  if (!data.pieces.length) {
    list.innerHTML = '<div class="text-slate-500 italic">Nessun pezzo.</div>';
    return;
  }
  data.pieces.forEach(p => {
    const card = document.createElement('a');
    card.href = `#/${encodeURIComponent(slug)}/${encodeURIComponent(p.filename)}`;
    card.className = 'piece-link block bg-slate-900/30 border border-slate-800/80 rounded-xl p-4 hover:border-slate-700 hover:bg-slate-900/60';
    const tags = (p.tags || []).slice(0, 6).map(t => `<span class="text-[10px] font-mono px-2 py-0.5 rounded-full bg-purple-950/40 text-purple-300 border border-purple-900/40">#${escapeHtml(t)}</span>`).join('');
    const stats = [];
    if (p.media_type) stats.push(p.media_type);
    if (p.view_count) stats.push(`${fmt(p.view_count)} view`);
    if (p.like_count) stats.push(`${fmt(p.like_count)} like`);
    card.innerHTML = `
      <div class="flex items-baseline justify-between gap-3 mb-1">
        <h3 class="text-base font-bold text-slate-100 leading-snug">${escapeHtml(p.title)}</h3>
        ${p.date ? `<span class="text-xs text-slate-500 shrink-0 font-mono">${p.date}</span>` : ''}
      </div>
      ${p.summary ? `<p class="text-sm text-slate-400 leading-relaxed line-clamp-2 mb-2">${escapeHtml(p.summary)}</p>` : ''}
      <div class="flex flex-wrap items-center gap-2 mt-2">
        ${tags}
        ${stats.length ? `<span class="text-[10px] text-slate-500 ml-auto">${stats.join(' · ')}</span>` : ''}
      </div>
    `;
    list.appendChild(card);
  });

  STATE.currentProfile = slug;
  highlightSidebar(slug);
}

async function loadPiece(slug, filename) {
  const data = await api(`/api/library/${encodeURIComponent(slug)}/${encodeURIComponent(filename)}`);
  if (!data) return;
  const fm = data.frontmatter || {};

  const tags = (fm.tags || []).map(t => `<span class="text-[10px] font-mono px-2 py-0.5 rounded-full bg-purple-950/40 text-purple-300 border border-purple-900/40">#${escapeHtml(t)}</span>`).join(' ');
  const hashtags = (fm.hashtags || []).map(t => `<span class="text-[10px] font-mono text-slate-500">#${escapeHtml(t)}</span>`).join(' ');

  const stats = [];
  if (fm.media_type) stats.push(['📦', fm.media_type]);
  if (fm.duration_sec) stats.push(['⏱️', `${fm.duration_sec}s`]);
  if (fm.view_count) stats.push(['👁️', fmt(fm.view_count)]);
  if (fm.like_count) stats.push(['❤️', fmt(fm.like_count)]);
  if (fm.comment_count) stats.push(['💬', fmt(fm.comment_count)]);
  if (fm.gemini_cost_usd != null) stats.push(['💸', '$' + Number(fm.gemini_cost_usd).toFixed(4)]);

  $('piece-meta').innerHTML = `
    <div class="flex flex-wrap items-baseline gap-x-4 gap-y-1 mb-3 text-sm">
      ${fm.profile ? `<span class="font-semibold text-purple-300">@${escapeHtml(fm.profile)}</span>` : ''}
      ${fm.date ? `<span class="text-slate-400 font-mono text-xs">${escapeHtml(fm.date)}</span>` : ''}
      ${fm.language ? `<span class="text-slate-500 text-xs">lingua: ${escapeHtml(fm.language)}</span>` : ''}
    </div>
    ${tags ? `<div class="flex flex-wrap gap-1.5 mb-2">${tags}</div>` : ''}
    ${hashtags ? `<div class="flex flex-wrap gap-2 mb-3">${hashtags}</div>` : ''}
    <div class="flex flex-wrap gap-4 text-sm text-slate-300 pt-2 border-t border-slate-800/40">
      ${stats.map(([ic, v]) => `<span class="inline-flex items-center gap-1.5"><span>${ic}</span><span class="font-mono text-xs">${escapeHtml(v)}</span></span>`).join('')}
    </div>
  `;

  // Render markdown
  marked.setOptions({ breaks: false, gfm: true });
  $('piece-body').innerHTML = marked.parse(data.body_markdown || '');

  // Footer
  if (fm.url) {
    $('piece-source').href = fm.url;
    $('piece-source').classList.remove('hidden');
  } else {
    $('piece-source').classList.add('hidden');
  }
  $('piece-filename').textContent = filename;
  $('back-label').textContent = `← ${fm.profile || slug}`;

  STATE.currentProfile = slug;
  STATE.currentPiece = filename;
  highlightSidebar(slug);
  window.scrollTo(0, 0);
}

function highlightSidebar(slug) {
  document.querySelectorAll('.profile-link').forEach(el => {
    el.classList.toggle('active', el.dataset.slug === slug);
  });
}

function goBack() {
  if (STATE.currentProfile) {
    location.hash = `#/${encodeURIComponent(STATE.currentProfile)}`;
  } else {
    location.hash = '';
  }
}

// =====================================================================
// Routing
// =====================================================================

async function route() {
  const h = location.hash.replace(/^#\/?/, '');
  if (!h) {
    showView('home');
    highlightSidebar(null);
    return;
  }
  const parts = h.split('/').map(decodeURIComponent);
  if (parts.length === 1) {
    showView('profile');
    await loadProfile(parts[0]);
  } else if (parts.length >= 2) {
    showView('piece');
    await loadPiece(parts[0], parts.slice(1).join('/'));
  }
}

window.addEventListener('hashchange', route);

// =====================================================================
// Budget mini
// =====================================================================

async function refreshBudget() {
  try {
    const r = await fetch('/api/budget');
    if (!r.ok) return;
    const data = await r.json();
    const s = data.status;
    $('mini-text').textContent = `$${s.spent_usd.toFixed(2)} / $${s.limit_usd.toFixed(0)} · ${s.percent.toFixed(0)}%`;
    $('mini-bar').style.width = Math.min(100, s.percent) + '%';
    if (s.exceeded) $('mini-bar').className = 'h-full bg-gradient-to-r from-red-500 to-rose-500';
    else if (s.warning) $('mini-bar').className = 'h-full bg-gradient-to-r from-amber-500 to-orange-500';
  } catch(e) { /* silent */ }
}

// =====================================================================
// Bootstrap
// =====================================================================

(async () => {
  await loadProfiles();
  await route();
  await refreshBudget();
  setInterval(refreshBudget, 15000);
})();
</script>
</body>
</html>
"""


@dashboard_bp.route("/api/scan", methods=["POST"])
def scan():
    """Scansiona un canale YouTube o profilo Instagram recuperando i video."""
    if not is_authenticated():
        return jsonify({"error": "Non autorizzato"}), 401
        
    data = request.json or {}
    target = data.get("url", "").strip()
    limit = int(data.get("limit", 10))
    
    if not target:
        return jsonify({"error": "Fornisci un URL valido"}), 400
        
    is_youtube = "youtube.com" in target or "youtu.be" in target or (target.startswith("http") and "instagram" not in target)
    
    items = []
    try:
        if is_youtube:
            # Assicuriamoci che canali o pagine utente vadano sul sotto-percorso corretto /videos se necessario
            if "/@" in target and not target.endswith("/videos") and not target.endswith("/shorts") and not "list=" in target:
                target = target.rstrip("/") + "/videos"
                
            logger.info(f"Scansione YouTube flat per: {target}")
            ydl_opts = {
                'extract_flat': True,
                'quiet': True,
                'playlistend': limit if limit > 0 else None
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                res = ydl.extract_info(target, download=False)
                entries = res.get('entries', []) or []
                
                for entry in entries:
                    if not entry:
                        continue
                    v_id = entry.get('id')
                    title = entry.get('title', 'Video senza titolo')
                    duration = entry.get('duration') or 0
                    
                    url = entry.get('url')
                    if not url and v_id:
                        url = f"https://www.youtube.com/watch?v={v_id}"
                        
                    # Prendi thumbnail
                    thumbnail = ""
                    if entry.get("thumbnails"):
                        thumbnail = entry["thumbnails"][-1]["url"]
                    elif v_id:
                        thumbnail = f"https://img.youtube.com/vi/{v_id}/hqdefault.jpg"
                        
                    if url:
                        items.append({
                            'id': v_id or url,
                            'url': url,
                            'title': title,
                            'duration': int(duration),
                            'thumbnail': thumbnail,
                            'media_type': 'video'
                        })
        else:
            # Per Instagram carichiamo i post via Apify (bypass blocchi Meta)
            username = target
            if "instagram.com" in username or "instagr.am" in username:
                m = re.search(r"instagram\.com/([a-zA-Z0-9_\.]+)", username)
                if m:
                    username = m.group(1)
            
            try:
                apify_token = os.getenv("APIFY_TOKEN")
                if not apify_token:
                    return jsonify({"error": "Per scaricare da Instagram occorre configurare APIFY_TOKEN nel file .env (vedi opzione Apify)."}), 400
                    
                from apify_client import ApifyClient
                client = ApifyClient(apify_token)
                
                logger.info(f"Avvio Apify scraper per: {username}")
                # Usiamo apify/instagram-scraper per aggirare i blocchi
                run_input = {
                    "directUrls": [f"https://www.instagram.com/{username}/"],
                    "resultsType": "posts",
                    "resultsLimit": limit if limit > 0 else 10,
                    "searchType": "hashtag",
                    "searchLimit": 1,
                }
                
                run = client.actor("apify/instagram-scraper").call(run_input=run_input)
                
                count = 0
                for item in client.dataset(run["defaultDatasetId"]).iterate_items():
                    if limit > 0 and count >= limit:
                        break
                    
                    # Estrai dati dal dataset Apify
                    post_url = item.get("url")
                    if not post_url:
                        continue
                        
                    is_video = item.get("isVideo", False) or item.get("type") == "Video"
                    media_type = 'video' if is_video else 'carousel'
                    duration = item.get("videoDuration", 30) if is_video else 0
                    shortcode = item.get("shortCode", "")
                    thumbnail = item.get("displayUrl", "")
                    timestamp = item.get("timestamp", "")
                    
                    items.append({
                        'id': shortcode or post_url,
                        'url': post_url,
                        'title': f"Instagram Post {timestamp[:10] if timestamp else shortcode}",
                        'duration': int(duration) if duration else 30,
                        'thumbnail': thumbnail,
                        'media_type': media_type
                    })
                    count += 1
                    
                if not items:
                    return jsonify({"error": "Nessun post trovato su Instagram o profilo privato."}), 404
                    
            except Exception as ie_error:
                logger.error(f"Errore Apify: {ie_error}")
                return jsonify({"error": f"Errore durante lo scraping Instagram con Apify: {str(ie_error)}"}), 500
                
        # Calcola costi stimati per ciascun elemento
        for item in items:
            if item['media_type'] == 'video' and item['duration'] > 0:
                input_tokens = item['duration'] * TOKENS_PER_VIDEO_SECOND
            else:
                input_tokens = 15000
                
            est_cost = ((input_tokens / 1_000_000) * GEMINI_INPUT_USD_PER_1M) + ((AVERAGE_OUTPUT_TOKENS / 1_000_000) * GEMINI_OUTPUT_USD_PER_1M)
            item['estimated_cost'] = round(est_cost, 4)
            item['tokens'] = int(input_tokens + AVERAGE_OUTPUT_TOKENS)
            
        return jsonify({"success": True, "items": items})
        
    except Exception as e:
        logger.error(f"Errore scansione URL: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@dashboard_bp.route("/api/process", methods=["POST"])
def process_queue():
    """Inizia l'elaborazione SSE degli elementi in coda selezionati."""
    if not is_authenticated():
        return Response("data: {\"event\": \"error\", \"msg\": \"Non autorizzato\"}\n\n", mimetype="text/event-stream")
        
    data = request.json or {}
    items = data.get("items", [])
    
    if not items:
        return jsonify({"error": "Nessun elemento selezionato"}), 400
        
    def _sse(payload):
        """Helper: formatta un dict come riga SSE."""
        return f"data: {json.dumps(payload)}\n\n"

    def sse_generator():
        yield _sse({"event": "start", "total": len(items)})
        
        bucket = get_storage_bucket()
        if not bucket:
            yield _sse({"event": "error", "msg": "Bucket GCS non configurato o inaccessibile"})
            return
            
        synthesizer = None
        try:
            synthesizer = KnowledgeSynthesizer()
        except Exception as se:
            yield _sse({"event": "error", "msg": "Errore Vertex AI Init: " + str(se)})
            return
            
        for index, item in enumerate(items):
            url = item["url"]
            title = item["title"]
            item_id = item["id"]
            
            yield _sse({"event": "item_start", "index": index, "id": item_id, "title": title})
            
            # 1. Download
            yield _sse({"event": "item_status", "id": item_id, "status": "Downloading video/post..."})
            
            h = MediaFetcher()._url_hash(url)
            session_dir = TEMP_WEB_DIR / f"web_session_{h}"
            session_dir.mkdir(parents=True, exist_ok=True)
            
            fetcher = MediaFetcher(temp_dir=session_dir, cache_file=TEMP_WEB_DIR / "download_cache.json")
            
            try:
                result = fetcher.fetch(url)
                media_type = result.get("media_type", "video")
                assets = result["assets"]
                
                # 2. Sintesi Gemini
                yield _sse({"event": "item_status", "id": item_id, "status": "Running Gemini multimodal analysis..."})
                
                if media_type == "video":
                    epistemic_data = synthesizer.generate_process_map_from_video(assets[0])
                    selected_frames = []
                else:
                    epistemic_data = synthesizer.generate_process_map_from_images(assets)
                    selected_frames = assets[:6]
                    
                # 3. Generazione HTML
                yield _sse({"event": "item_status", "id": item_id, "status": "Compiling HTML map..."})
                
                map_title = epistemic_data.get("title", title)
                executive_summary = epistemic_data.get("executive_summary", "")
                steps = epistemic_data.get("steps", [])
                epistemic_insights = epistemic_data.get("epistemic_insights", [])
                
                html_code = generate_epistemic_html(
                    title=map_title,
                    executive_summary=executive_summary,
                    steps=steps,
                    epistemic_insights=epistemic_insights,
                    image_paths=selected_frames
                )
                
                # 4. Upload del report su GCS
                yield _sse({"event": "item_status", "id": item_id, "status": "Saving report to Google Cloud Storage..."})
                
                safe_title = re.sub(r'[^\w\-]', '_', map_title)[:40]
                html_filename = f"processo_{safe_title}_{h}.html"
                
                blob = bucket.blob(f"reports/{html_filename}")
                blob.upload_from_string(html_code, content_type="text/html")
                
                yield _sse({
                    "event": "item_success",
                    "index": index,
                    "id": item_id,
                    "filename": html_filename,
                    "title": map_title
                })
                
            except Exception as exc:
                logger.error(f"Errore elaborazione {title}: {exc}", exc_info=True)
                yield _sse({"event": "item_failed", "index": index, "id": item_id, "error": str(exc)})
                
            finally:
                # Cleanup sessione
                try:
                    if session_dir.exists():
                        shutil.rmtree(session_dir)
                except Exception:
                    pass
                    
        yield _sse({"event": "completed"})

    return Response(sse_generator(), mimetype="text/event-stream")

@dashboard_bp.route("/api/reports", methods=["GET"])
def list_reports():
    """Elenca tutti i report HTML memorizzati in GCS."""
    if not is_authenticated():
        return jsonify({"error": "Non autorizzato"}), 401
        
    bucket = get_storage_bucket()
    if not bucket:
        return jsonify({"error": "Bucket GCS inaccessibile"}), 500
        
    try:
        blobs = bucket.list_blobs(prefix="reports/")
        reports = []
        for blob in blobs:
            if not blob.name.endswith(".html"):
                continue
            filename = blob.name.replace("reports/", "")
            reports.append({
                "filename": filename,
                "size_kb": round(blob.size / 1024, 1),
                "created": blob.time_created.strftime("%Y-%m-%d %H:%M") if blob.time_created else "N/D"
            })
            
        # Ordina per data decrescente
        reports.sort(key=lambda x: x.get("created", ""), reverse=True)
        return jsonify({"success": True, "reports": reports})
    except Exception as e:
        logger.error(f"Errore recupero report: {e}")
        return jsonify({"error": str(e)}), 500

@dashboard_bp.route("/reports/<filename>", methods=["GET"])
def view_report(filename):
    """Visualizza o scarica un report direttamente da GCS."""
    if not is_authenticated():
        return "Non autorizzato", 401
        
    bucket = get_storage_bucket()
    if not bucket:
        return "GCS Bucket non configurato", 500
        
    try:
        blob = bucket.blob(f"reports/{filename}")
        if not blob.exists():
            return "Report non trovato", 404
            
        html_content = blob.download_as_text()
        return Response(html_content, mimetype="text/html")
    except Exception as e:
        logger.error(f"Errore download report {filename}: {e}")
        return f"Errore: {str(e)}", 500

@dashboard_bp.route("/api/delete_report", methods=["POST"])
def delete_report():
    """Elimina un report dal bucket GCS."""
    if not is_authenticated():
        return jsonify({"error": "Non autorizzato"}), 401
        
    data = request.json or {}
    filename = data.get("filename", "")
    
    if not filename:
        return jsonify({"error": "Nome file non specificato"}), 400
        
    bucket = get_storage_bucket()
    if not bucket:
        return jsonify({"error": "GCS Bucket non configurato"}), 500
        
    try:
        blob = bucket.blob(f"reports/{filename}")
        if blob.exists():
            blob.delete()
            return jsonify({"success": True})
        return jsonify({"error": "File non trovato"}), 404
    except Exception as e:
        logger.error(f"Errore cancellazione report: {e}")
        return jsonify({"error": str(e)}), 500

# =====================================================================
# Interfaccia Utente (Dashboard SPA)
# =====================================================================

@dashboard_bp.route("/", methods=["GET"])
def render_dashboard():
    """Mostra la pagina della dashboard (con login integrato)."""
    # Se il passcode è disattivato del tutto (nessuno impostato in .env), entra subito
    allowed_id = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    db_pass = os.getenv("DASHBOARD_PASSWORD")
    no_auth = not allowed_id and not db_pass
    
    auth_state = "true" if (is_authenticated() or no_auth) else "false"
    
    # HTML SPA della dashboard
    dashboard_html = """
<!DOCTYPE html>
<html lang="it" class="h-full">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Antigravity Knowledge Hub</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Google Fonts: Outfit (Header) & Inter (Body) -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #030712;
            color: #f3f4f6;
        }
        h1, h2, h3, h4, .font-outfit {
            font-family: 'Outfit', sans-serif;
        }
        .glass-panel {
            background: rgba(17, 24, 39, 0.45);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
        .glass-card {
            background: rgba(31, 41, 55, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.03);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .glass-card:hover {
            border-color: rgba(147, 51, 234, 0.3);
            box-shadow: 0 10px 25px -5px rgba(147, 51, 234, 0.1);
        }
    </style>
</head>
<body class="h-full flex flex-col overflow-hidden bg-radial-at-t from-slate-900 via-gray-950 to-neutral-950">

    <!-- Schermata di Login -->
    <div id="login-screen" class="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/95 backdrop-blur-md transition-opacity duration-300" style="display: none;">
        <div class="glass-panel p-8 rounded-3xl w-full max-w-md shadow-2xl text-center space-y-6">
            <div class="inline-flex p-4 bg-purple-500/10 text-purple-400 rounded-full mb-2">
                <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path>
                </svg>
            </div>
            <div>
                <h2 class="text-2xl font-extrabold text-white">Knowledge Hub</h2>
                <p class="text-sm text-slate-400 mt-1">Inserisci il passcode configurato per accedere</p>
            </div>
            <div class="space-y-4">
                <input type="password" id="passcode-input" placeholder="Passcode (es. User ID Telegram)" class="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-3 text-center text-white focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition">
                <button onclick="attemptLogin()" class="w-full bg-purple-600 hover:bg-purple-700 text-white font-bold py-3 rounded-xl shadow-lg transition duration-200">
                    Sblocca Hub
                </button>
                <p id="login-error" class="text-xs text-red-400" style="display: none;"></p>
            </div>
        </div>
    </div>

    <!-- Main App Container -->
    <div id="app-container" class="h-full flex flex-col md:flex-row overflow-hidden" style="display: none;">
        
        <!-- Sidebar Navigation -->
        <aside class="md:w-64 bg-slate-950/60 border-b md:border-b-0 md:border-r border-slate-850 p-6 flex flex-col justify-between glass-panel">
            <div class="space-y-8">
                <!-- Logo -->
                <div class="flex items-center gap-3">
                    <div class="p-2 bg-gradient-to-tr from-purple-600 to-indigo-500 rounded-xl text-white shadow-lg">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path>
                        </svg>
                    </div>
                    <div>
                        <h1 class="text-lg font-extrabold text-white tracking-tight">Antigravity</h1>
                        <span class="text-xxs uppercase tracking-widest text-purple-400 font-semibold">Knowledge Hub</span>
                    </div>
                </div>

                <!-- Nav -->
                <nav class="space-y-1.5">
                    <button onclick="switchTab('tab-dashboard')" id="btn-tab-dashboard" class="nav-btn w-full flex items-center gap-3 px-4 py-3 rounded-xl font-medium text-slate-300 hover:bg-slate-900/60 hover:text-white transition duration-200">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2v-4zM14 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2v-4z"></path>
                        </svg>
                        <span>Dashboard</span>
                    </button>
                    <button onclick="switchTab('tab-gallery')" id="btn-tab-gallery" class="nav-btn w-full flex items-center gap-3 px-4 py-3 rounded-xl font-medium text-slate-300 hover:bg-slate-900/60 hover:text-white transition duration-200">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path>
                        </svg>
                        <span>Galleria Report</span>
                    </button>
                </nav>
            </div>

            <!-- Footer Sidebar -->
            <div class="pt-6 border-t border-slate-900 flex items-center justify-between">
                <div class="flex items-center gap-2">
                    <div class="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></div>
                    <span class="text-xs text-slate-400">Vertex AI Online</span>
                </div>
                <button onclick="logout()" class="text-xs text-red-400 hover:text-red-300 transition">Logout</button>
            </div>
        </aside>

        <!-- Main Content Panel -->
        <main class="flex-1 flex flex-col overflow-hidden p-6 md:p-8">

            <!-- TAB: DASHBOARD -->
            <section id="tab-dashboard" class="tab-content flex-1 flex flex-col md:flex-row gap-6 overflow-hidden" style="display: none;">
                
                <!-- Sinistra: Input e Coda -->
                <div class="flex-1 flex flex-col space-y-6 overflow-hidden">
                    <div class="glass-panel p-6 rounded-3xl space-y-4">
                        <h2 class="text-xl font-bold text-white">Scansiona Canale o Profilo</h2>
                        <div class="flex flex-col md:flex-row gap-3">
                            <input type="text" id="target-url" placeholder="URL Canale YouTube o Username Instagram" class="flex-1 bg-slate-900/60 border border-slate-800 rounded-xl px-4 py-3 text-white focus:outline-none focus:ring-2 focus:ring-purple-500">
                            <div class="w-full md:w-32">
                                <input type="number" id="target-limit" value="10" min="1" max="50" placeholder="Limite" class="w-full bg-slate-900/60 border border-slate-800 rounded-xl px-4 py-3 text-center text-white focus:outline-none focus:ring-2 focus:ring-purple-500">
                            </div>
                            <button onclick="scanTarget()" id="btn-scan" class="bg-purple-600 hover:bg-purple-700 text-white font-bold px-6 py-3 rounded-xl shadow-lg shadow-purple-950/20 active:scale-95 transition flex items-center justify-center gap-2">
                                <span id="scan-spinner" class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" style="display: none;"></span>
                                <span>Scansiona</span>
                            </button>
                        </div>
                    </div>

                    <!-- Risultati Scansione -->
                    <div class="flex-1 glass-panel p-6 rounded-3xl flex flex-col overflow-hidden">
                        <div class="flex items-center justify-between mb-4">
                            <h3 class="text-lg font-bold text-white">Elementi Rilevati</h3>
                            <div class="flex items-center gap-3 text-sm">
                                <button onclick="toggleSelectAll(true)" class="text-purple-400 hover:underline">Seleziona Tutti</button>
                                <span class="text-slate-600">|</span>
                                <button onclick="toggleSelectAll(false)" class="text-purple-400 hover:underline font-normal">Deseleziona</button>
                            </div>
                        </div>

                        <!-- Lista in scroll -->
                        <div id="scan-results-list" class="flex-1 overflow-y-auto space-y-3 pr-2">
                            <!-- Placeholder -->
                            <div class="h-full flex flex-col items-center justify-center text-slate-500 py-12">
                                <svg class="w-12 h-12 mb-3 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                                </svg>
                                <p>Inserisci un URL sopra per scansionare i video disponibili.</p>
                            </div>
                        </div>

                        <!-- Footer Elaborazione Coda -->
                        <div id="queue-actions-footer" class="mt-4 pt-4 border-t border-slate-900/80 flex items-center justify-between" style="display: none;">
                            <div>
                                <span class="text-xs uppercase tracking-widest text-slate-400 font-semibold block">Spesa Stimata</span>
                                <span class="text-xl font-extrabold text-emerald-400" id="selected-cost">$0.00 USD</span>
                                <span class="text-xs text-slate-500 ml-1" id="selected-count">(0 elementi)</span>
                            </div>
                            <button onclick="processSelected()" class="bg-gradient-to-r from-purple-600 to-indigo-500 hover:from-purple-700 hover:to-indigo-600 text-white font-bold px-8 py-3.5 rounded-xl shadow-lg active:scale-95 transition">
                                Elabora Selezionati
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Destra: Pannello Progressi Elaborazione -->
                <div class="w-full md:w-80 flex flex-col space-y-6 overflow-hidden" id="processing-panel" style="display: none;">
                    <div class="glass-panel p-6 rounded-3xl flex flex-col h-full overflow-hidden">
                        <h3 class="text-lg font-bold text-white mb-2">Stato Elaborazione</h3>
                        <div class="w-full bg-slate-900 rounded-full h-2.5 mb-4">
                            <div class="bg-purple-600 h-2.5 rounded-full transition-all duration-300" id="progress-bar" style="width: 0%"></div>
                        </div>
                        <div class="flex-1 overflow-y-auto space-y-3 pr-2 text-sm" id="progress-logs">
                            <!-- Logs dynamically appended here -->
                        </div>
                    </div>
                </div>

            </section>

            <!-- TAB: GALLERIA -->
            <section id="tab-gallery" class="tab-content flex-1 flex flex-col space-y-6 overflow-hidden" style="display: none;">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div>
                        <h2 class="text-2xl font-extrabold text-white">I Tuoi Report</h2>
                        <p class="text-sm text-slate-400">Visualizza, copia ed elimina le mappe dei processi persistite in GCS</p>
                    </div>
                    <div class="flex items-center gap-3">
                        <input type="text" id="gallery-search" oninput="filterGallery()" placeholder="Cerca tra i report..." class="bg-slate-900/60 border border-slate-800 rounded-xl px-4 py-2.5 text-white w-64 focus:outline-none focus:ring-2 focus:ring-purple-500">
                        <button onclick="loadReports()" class="p-2.5 bg-slate-900 hover:bg-slate-850 rounded-xl text-slate-300 hover:text-white transition">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 8H18.2M7 9a7 7 0 0111.758-3.176M7 9h6"></path>
                            </svg>
                        </button>
                    </div>
                </div>

                <!-- Lista Report -->
                <div class="flex-1 overflow-y-auto pr-2">
                    <div id="reports-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        <!-- Report cards will appear here -->
                    </div>
                    <div id="reports-empty-state" class="h-64 flex flex-col items-center justify-center text-slate-500" style="display: none;">
                        <svg class="w-12 h-12 mb-3 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                        </svg>
                        <p>Nessun report generato finora. Vai nella Dashboard per iniziare.</p>
                    </div>
                </div>
            </section>

        </main>
    </div>

    <!-- Scripting Frontend Dashboard -->
    <script>
        const AUTH_STATE = __AUTH_STATE__;
        let scannedItems = [];

        window.onload = function() {
            if (AUTH_STATE) {
                document.getElementById('app-container').style.display = 'flex';
                switchTab('tab-dashboard');
            } else {
                document.getElementById('login-screen').style.display = 'flex';
            }
        };

        // Autenticazione
        async function attemptLogin() {
            const passcode = document.getElementById('passcode-input').value.trim();
            const errEl = document.getElementById('login-error');
            errEl.style.display = 'none';

            try {
                const res = await fetch('/api/auth', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({passcode})
                });
                if (res.ok) {
                    document.getElementById('login-screen').style.opacity = '0';
                    setTimeout(() => {
                        document.getElementById('login-screen').style.display = 'none';
                        document.getElementById('app-container').style.display = 'flex';
                        switchTab('tab-dashboard');
                    }, 300);
                } else {
                    const data = await res.json();
                    errEl.innerText = data.error || 'Autenticazione fallita';
                    errEl.style.display = 'block';
                }
            } catch (e) {
                errEl.innerText = 'Errore di connessione';
                errEl.style.display = 'block';
            }
        }

        async function logout() {
            await fetch('/api/logout', {method: 'POST'});
            window.location.reload();
        }

        // Cambio Tab
        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
            document.getElementById(tabId).style.display = tabId === 'tab-dashboard' ? 'flex' : 'block';

            document.querySelectorAll('.nav-btn').forEach(btn => {
                btn.classList.remove('bg-purple-600/10', 'text-purple-400');
                btn.classList.add('text-slate-300');
            });
            document.getElementById('btn-' + tabId).classList.add('bg-purple-600/10', 'text-purple-400');
            document.getElementById('btn-' + tabId).classList.remove('text-slate-300');

            if (tabId === 'tab-gallery') {
                loadReports();
            }
        }

        // Scansione URL
        async function scanTarget() {
            const url = document.getElementById('target-url').value.trim();
            const limit = document.getElementById('target-limit').value;
            if (!url) return alert('Inserisci un URL valido');

            document.getElementById('btn-scan').disabled = true;
            document.getElementById('scan-spinner').style.display = 'block';
            document.getElementById('scan-results-list').innerHTML = '<div class="h-64 flex items-center justify-center text-slate-400 animate-pulse">Scansione del canale in corso... Recupero dei video...</div>';
            document.getElementById('queue-actions-footer').style.display = 'none';

            try {
                const res = await fetch('/api/scan', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url, limit})
                });
                const data = await res.json();
                if (res.ok) {
                    scannedItems = data.items;
                    renderScannedItems();
                } else {
                    document.getElementById('scan-results-list').innerHTML = `<div class="h-64 flex items-center justify-center text-red-400 text-center px-6">${data.error || 'Errore durante la scansione'}</div>`;
                }
            } catch (e) {
                document.getElementById('scan-results-list').innerHTML = `<div class="h-64 flex items-center justify-center text-red-400">Impossibile connettersi al server.</div>`;
            } finally {
                document.getElementById('btn-scan').disabled = false;
                document.getElementById('scan-spinner').style.display = 'none';
            }
        }

        function renderScannedItems() {
            const listEl = document.getElementById('scan-results-list');
            if (scannedItems.length === 0) {
                listEl.innerHTML = '<div class="h-64 flex items-center justify-center text-slate-500">Nessun video trovato.</div>';
                document.getElementById('queue-actions-footer').style.display = 'none';
                return;
            }

            listEl.innerHTML = scannedItems.map((item, idx) => `
                <div class="glass-card p-4 rounded-2xl flex items-center gap-4">
                    <input type="checkbox" id="chk-${item.id}" checked onchange="updateQueueStats()" class="w-5 h-5 accent-purple-600 rounded cursor-pointer">
                    ${item.thumbnail ? `<img src="${item.thumbnail}" class="w-24 h-16 object-cover rounded-lg bg-slate-900 border border-slate-800" onerror="this.src=''">` : ''}
                    <div class="flex-1 min-w-0">
                        <h4 class="font-bold text-white text-sm truncate">${item.title}</h4>
                        <div class="flex items-center gap-3 text-xs text-slate-400 mt-1">
                            <span class="bg-slate-800 px-2 py-0.5 rounded text-xxs font-semibold uppercase">${item.media_type}</span>
                            ${item.duration > 0 ? `<span>⏱️ ${formatDuration(item.duration)}</span>` : ''}
                            <span>Est: <strong class="text-emerald-400">$${item.estimated_cost}</strong></span>
                        </div>
                    </div>
                </div>
            `).join('');

            document.getElementById('queue-actions-footer').style.display = 'flex';
            updateQueueStats();
        }

        function formatDuration(sec) {
            const mins = Math.floor(sec / 60);
            const secs = sec % 60;
            return `${mins}:${secs.toString().padStart(2, '0')}`;
        }

        function toggleSelectAll(select) {
            scannedItems.forEach(item => {
                const el = document.getElementById(`chk-${item.id}`);
                if (el) el.checked = select;
            });
            updateQueueStats();
        }

        function updateQueueStats() {
            let totalCost = 0;
            let count = 0;

            scannedItems.forEach(item => {
                const el = document.getElementById(`chk-${item.id}`);
                if (el && el.checked) {
                    totalCost += item.estimated_cost;
                    count++;
                }
            });

            document.getElementById('selected-cost').innerText = `$${totalCost.toFixed(4)} USD`;
            document.getElementById('selected-count').innerText = `(${count} elementi selezionati)`;
        }

        // Processamento coda SSE
        function processSelected() {
            const selected = scannedItems.filter(item => {
                const el = document.getElementById(`chk-${item.id}`);
                return el && el.checked;
            });

            if (selected.length === 0) return alert('Seleziona almeno un elemento da elaborare');

            // Mostra il pannello di progresso a destra
            document.getElementById('processing-panel').style.display = 'flex';
            const logsEl = document.getElementById('progress-logs');
            logsEl.innerHTML = '<div class="text-slate-400">Avvio del processore di coda...</div>';
            document.getElementById('progress-bar').style.width = '0%';

            fetch('/api/process', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({items: selected})
            }).then(response => {
                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';

                function read() {
                    reader.read().then(({done, value}) => {
                        if (done) return;
                        buffer += decoder.decode(value, {stream: true});
                        const lines = buffer.split('\\n\\n');
                        buffer = lines.pop(); // tieni l'ultima linea incompleta

                        lines.forEach(line => {
                            if (line.startsWith('data: ')) {
                                try {
                                    const data = JSON.parse(line.slice(6));
                                    handleSSEEvent(data, selected.length);
                                } catch (e) {}
                            }
                        });
                        read();
                    });
                }
                read();
            });
        }

        function handleSSEEvent(data, totalCount) {
            const logsEl = document.getElementById('progress-logs');
            const barEl = document.getElementById('progress-bar');

            if (data.event === 'start') {
                logsEl.innerHTML += `<div class="text-purple-400 font-semibold mt-2">Coda avviata. Totale elementi: ${data.total}</div>`;
            } else if (data.event === 'item_start') {
                logsEl.innerHTML += `<div class="text-white font-medium mt-3 border-t border-slate-900 pt-2">[${data.index + 1}/${totalCount}] Elaborazione: ${data.title}...</div>`;
                logsEl.scrollTop = logsEl.scrollHeight;
            } else if (data.event === 'item_status') {
                logsEl.innerHTML += `<div class="text-slate-400 text-xs pl-4">• ${data.status}</div>`;
                logsEl.scrollTop = logsEl.scrollHeight;
            } else if (data.event === 'item_success') {
                logsEl.innerHTML += `<div class="text-emerald-400 font-medium pl-4">✓ Completato! <a href="/reports/${data.filename}" target="_blank" class="underline font-semibold text-purple-400">Apri Report</a></div>`;
                const progressPct = ((data.index + 1) / totalCount) * 100;
                barEl.style.width = `${progressPct}%`;
                logsEl.scrollTop = logsEl.scrollHeight;
            } else if (data.event === 'item_failed') {
                logsEl.innerHTML += `<div class="text-red-400 font-medium pl-4">✗ Fallito: ${data.error}</div>`;
                const progressPct = ((data.index + 1) / totalCount) * 100;
                barEl.style.width = `${progressPct}%`;
                logsEl.scrollTop = logsEl.scrollHeight;
            } else if (data.event === 'completed') {
                logsEl.innerHTML += `<div class="text-emerald-400 font-bold mt-4 border-t border-slate-900 pt-2">✨ Coda completata con successo!</div>`;
                logsEl.scrollTop = logsEl.scrollHeight;
            } else if (data.event === 'error') {
                logsEl.innerHTML += `<div class="text-red-500 font-bold mt-2">Errore critico: ${data.msg}</div>`;
            }
        }

        // Galleria Report
        async function loadReports() {
            const gridEl = document.getElementById('reports-grid');
            const emptyEl = document.getElementById('reports-empty-state');
            gridEl.innerHTML = '';
            emptyEl.style.display = 'none';

            try {
                const res = await fetch('/api/reports');
                const data = await res.json();
                if (res.ok && data.reports.length > 0) {
                    window.allReports = data.reports;
                    renderReports(data.reports);
                } else {
                    emptyEl.style.display = 'flex';
                }
            } catch (e) {
                gridEl.innerHTML = '<div class="text-red-400 col-span-3 text-center">Impossibile recuperare i report da GCS.</div>';
            }
        }

        function renderReports(reports) {
            const gridEl = document.getElementById('reports-grid');
            gridEl.innerHTML = reports.map(rep => {
                // Genera un titolo più leggibile dal nome file
                let cleanTitle = rep.filename
                    .replace(/^processo_/, '')
                    .replace(/_[a-f0-9]{16}\\.html$/, '')
                    .replace(/_/g, ' ');
                if (cleanTitle.length > 55) cleanTitle = cleanTitle.substring(0, 52) + '...';

                return `
                    <div class="glass-panel p-5 rounded-2xl flex flex-col justify-between h-48 hover:border-purple-600/30 transition duration-300 report-card" data-filename="${rep.filename}">
                        <div class="space-y-2">
                            <span class="text-xxs uppercase tracking-wider text-purple-400 font-bold">Mappa di Processo</span>
                            <h4 class="font-extrabold text-white text-md line-clamp-2">${cleanTitle}</h4>
                            <div class="flex gap-3 text-xxs text-slate-500">
                                <span>📦 ${rep.size_kb} KB</span>
                                <span>📅 ${rep.created}</span>
                            </div>
                        </div>
                        <div class="flex items-center gap-2 mt-4 pt-3 border-t border-slate-900/80">
                            <a href="/reports/${rep.filename}" target="_blank" class="flex-1 bg-purple-600 hover:bg-purple-700 text-center text-white text-xs font-bold py-2 rounded-xl transition">
                                Apri
                            </a>
                            <button onclick="copyLink('${rep.filename}')" class="p-2 bg-slate-900 hover:bg-slate-850 rounded-xl text-slate-400 hover:text-white transition" title="Copia link per AI">
                                🔗
                            </button>
                            <button onclick="deleteReport('${rep.filename}')" class="p-2 bg-slate-900 hover:bg-red-950/60 rounded-xl text-slate-400 hover:text-red-400 transition" title="Elimina report">
                                🗑️
                            </button>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function filterGallery() {
            const q = document.getElementById('gallery-search').value.toLowerCase().trim();
            if (!window.allReports) return;
            const filtered = window.allReports.filter(rep => rep.filename.toLowerCase().includes(q));
            renderReports(filtered);
        }

        async function deleteReport(filename) {
            if (!confirm('Sei sicuro di voler eliminare definitivamente questo report da GCS?')) return;
            try {
                const res = await fetch('/api/delete_report', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({filename})
                });
                if (res.ok) {
                    loadReports();
                } else {
                    alert('Errore durante l\\'eliminazione del report');
                }
            } catch (e) {
                alert('Errore di connessione');
            }
        }

        function copyLink(filename) {
            const fullUrl = window.location.origin + '/reports/' + filename;
            navigator.clipboard.writeText(fullUrl).then(() => {
                alert('Link copiato negli appunti! Ora puoi incollarlo in ChatGPT o Claude per fargli leggere la mappa del processo.');
            }).catch(err => {
                alert('Impossibile copiare il link negli appunti.');
            });
        }
    </script>
</body>
</html>
    """.replace("__AUTH_STATE__", auth_state)
    
    return render_template_string(dashboard_html)
