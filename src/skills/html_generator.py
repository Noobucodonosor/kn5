import json
import base64
from pathlib import Path
from typing import List, Dict, Optional

def generate_epistemic_html(
    title: str,
    executive_summary: str,
    steps: List[Dict],
    epistemic_insights: List[Dict],
    image_paths: Optional[List[str]] = None
) -> str:
    """
    Genera un file HTML standalone e responsive focalizzato sulla spiegazione chiara e strutturata di un processo.
    Rappresenta l'immagine del contenuto informativo del Reel/Carosello sotto forma di testo leggibile
    sia dall'utente che da altre AI (grazie a un Markdown integrato e copiabile).
    """
    
    # Prepara le immagini come Base64
    images_html = ""
    if image_paths:
        images_html = '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mt-6">'
        for i, img_path in enumerate(image_paths):
            p = Path(img_path)
            if p.exists():
                try:
                    with open(p, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode("utf-8")
                    suffix = p.suffix.lower()
                    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
                    base64_url = f"data:{mime};base64,{img_data}"
                    images_html += f"""
                    <div class="bg-slate-800 border border-slate-700/50 rounded-2xl overflow-hidden shadow-lg">
                        <div class="p-3 text-xs text-slate-400 border-b border-slate-700 bg-slate-900/50 font-medium">Frame {i+1}</div>
                        <img src="{base64_url}" alt="Frame {i+1}" class="w-full h-auto object-cover max-h-64" />
                    </div>
                    """
                except Exception as e:
                    pass
        images_html += '</div>'

    # Prepara la lista visuale e markdown dei passaggi
    steps_html = ""
    steps_md = ""
    for s in steps:
        num = s.get("step_number", 1)
        s_title = s.get("title", f"Passo {num}")
        s_desc = s.get("description", "")
        actions = s.get("key_actions", [])
        
        # Formattazione azioni concrete
        actions_html = ""
        actions_md = ""
        if actions:
            actions_html = '<div class="mt-4 p-4 bg-slate-900/50 border border-slate-700 rounded-xl"><span class="font-bold text-xs uppercase tracking-wider text-purple-400 block mb-2">Azioni Chiave</span><ul class="list-disc pl-5 space-y-1 text-slate-300 text-sm">'
            for act in actions:
                actions_html += f"<li>{act}</li>"
                actions_md += f"  - {act}\n"
            actions_html += '</ul></div>'
            
        steps_html += f"""
        <div class="flex gap-6 items-start pb-8 border-l-2 border-purple-500/20 last:border-0 pl-6 relative">
            <!-- Cerchio con Numero -->
            <div class="absolute -left-[17px] top-0 w-8 h-8 rounded-full bg-purple-600 border-4 border-slate-950 flex items-center justify-center font-bold text-sm text-white shadow-md shadow-purple-900/30">
                {num}
            </div>
            
            <div class="flex-1 bg-slate-900/30 border border-slate-800/80 rounded-2xl p-6 hover:border-purple-500/30 transition duration-300 shadow-md">
                <h3 class="text-xl font-bold text-white mb-3">{s_title}</h3>
                <p class="text-slate-300 text-base leading-relaxed">{s_desc}</p>
                {actions_html}
            </div>
        </div>
        """
        
        steps_md += f"### Fase {num}: {s_title}\n{s_desc}\n"
        if actions_md:
            steps_md += f"**Azioni concrete da compiere**:\n{actions_md}"
        steps_md += "\n"

    # Prepara gli approfondimenti teorici
    insights_html = ""
    insights_md = ""
    for ins in epistemic_insights:
        concept = ins.get("concept", "Concetto")
        explanation = ins.get("explanation", "")
        
        insights_html += f"""
        <div class="bg-slate-900/30 border border-slate-800/80 rounded-2xl p-6 hover:border-blue-500/30 transition duration-300 shadow-md">
            <h4 class="text-lg font-bold text-blue-400 mb-2">{concept}</h4>
            <p class="text-slate-300 text-sm leading-relaxed">{explanation}</p>
        </div>
        """
        insights_md += f"- **{concept}**: {explanation}\n"

    # Formatta il testo in Markdown ottimizzato per l'AI
    ai_markdown = f"""# {title}

## SINTESI DEL PROCESSO
{executive_summary}

## FASI DEL PROCESSO (STEP-BY-STEP)
{steps_md}

## PRINCIPI EPISTEMICI E METODI CHIAVE
{insights_md if insights_md else 'Nessuno definito.'}
"""

    # Template HTML completo
    html_content = f"""<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Google Fonts: Outfit & Inter -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        body {{
            font-family: 'Inter', sans-serif;
            background-color: #090d16;
            color: #f8fafc;
        }}
        h1, h2, h3, h4, .font-outfit {{
            font-family: 'Outfit', sans-serif;
        }}
        /* Personalizzazione scrollbar */
        ::-webkit-scrollbar {{
            width: 8px;
            height: 8px;
        }}
        ::-webkit-scrollbar-track {{
            background: #090d16;
        }}
        ::-webkit-scrollbar-thumb {{
            background: #1e293b;
            border-radius: 4px;
        }}
    </style>
</head>
<body class="min-h-screen pb-16">
    <!-- Header -->
    <header class="border-b border-slate-800/80 bg-slate-900/20 backdrop-blur-md sticky top-0 z-50">
        <div class="max-w-4xl mx-auto px-6 py-6 flex flex-row items-center justify-between gap-4">
            <div>
                <span class="text-xs uppercase tracking-widest text-purple-400 font-semibold">Mappa di Processo</span>
                <h1 class="text-2xl md:text-3xl font-extrabold text-white mt-0.5">{title}</h1>
            </div>
            <div class="flex items-center gap-3">
                <button onclick="copyToAI()" class="px-5 py-2.5 rounded-xl bg-purple-600 hover:bg-purple-700 active:scale-95 text-white font-bold text-sm shadow-lg shadow-purple-950/40 transition duration-150 flex items-center gap-2">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m-5 4h5m-5 4h5m1 2h-1m-4 0h.01"></path>
                    </svg>
                    <span>Copia per AI</span>
                </button>
                <span id="copy-status" class="text-xs text-purple-400 font-semibold hidden animate-pulse">Copiato!</span>
            </div>
        </div>
    </header>

    <!-- Main Content -->
    <main class="max-w-4xl mx-auto px-6 mt-10 space-y-12">
        
        <!-- Sintesi -->
        <section class="bg-gradient-to-br from-purple-950/20 to-slate-900/50 border border-purple-500/15 rounded-3xl p-8 shadow-xl">
            <h2 class="text-xl font-bold text-purple-300 mb-3 flex items-center gap-2">
                <svg class="w-5 h-5 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                </svg>
                Sintesi del Processo
            </h2>
            <p class="text-slate-200 text-lg leading-relaxed">{executive_summary}</p>
        </section>

        <!-- Fasi Step-by-Step -->
        <section>
            <h2 class="text-2xl font-bold text-white mb-8 flex items-center gap-2">
                <svg class="w-6 h-6 text-purple-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"></path>
                </svg>
                Flusso del Processo
            </h2>
            <div class="space-y-4">
                {steps_html}
            </div>
        </section>

        <!-- Principi Chiave (Epistemic Insights) -->
        {f'''
        <section>
            <h2 class="text-2xl font-bold text-white mb-6 flex items-center gap-2">
                <svg class="w-6 h-6 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"></path>
                </svg>
                Principi & Concetti Chiave
            </h2>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                {insights_html}
            </div>
        </section>
        ''' if insights_html else ''}

        <!-- Frame ed Immagini -->
        {f'''
        <section>
            <h2 class="text-2xl font-bold text-white mb-6 flex items-center gap-2">
                <svg class="w-6 h-6 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path>
                </svg>
                Fotogrammi di Riferimento
            </h2>
            {images_html}
        </section>
        ''' if images_html else ''}

    </main>

    <!-- Contenuto in formato Markdown nascosto per l'AI -->
    <textarea id="ai-markdown" class="hidden">{ai_markdown}</textarea>

    <footer class="max-w-4xl mx-auto px-6 mt-16 pt-8 border-t border-slate-800/80 text-center text-slate-500 text-xs">
        Dati generati e pronti per l'importazione AI &bull; DanyKnowledgeBot
    </footer>

    <!-- Script per copiare il testo -->
    <script>
        function copyToAI() {{
            const markdownText = document.getElementById('ai-markdown').value;
            navigator.clipboard.writeText(markdownText).then(function() {{
                const status = document.getElementById('copy-status');
                status.classList.remove('hidden');
                setTimeout(function() {{
                    status.classList.add('hidden');
                }}, 2000);
            }}, function(err) {{
                alert('Impossibile copiare il testo: ', err);
            }});
        }}
    </script>
</body>
</html>
"""
    return html_content
