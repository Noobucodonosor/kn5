"""Skills package per kn5.

Gli export pubblici sono caricati solo quando vengono richiesti. In questo modo
un modulo puro (per esempio ``process_map``) non importa anche SDK Google,
client cloud e downloader, rendendo test e strumenti locali rapidi e isolati.
"""

from importlib import import_module

__all__ = [
    "BudgetTracker",
    "BudgetExceeded",
    "KnowledgeSynthesizer",
    "KnowledgeSynthesizerError",
    "LibraryWriter",
    "MediaFetcher",
]

_EXPORTS = {
    "BudgetTracker": (".budget", "BudgetTracker"),
    "BudgetExceeded": (".budget", "BudgetExceeded"),
    "KnowledgeSynthesizer": (".knowledge_synthesizer", "KnowledgeSynthesizer"),
    "KnowledgeSynthesizerError": (".knowledge_synthesizer", "KnowledgeSynthesizerError"),
    "LibraryWriter": (".library_writer", "LibraryWriter"),
    "MediaFetcher": (".media_fetcher", "MediaFetcher"),
}


def __getattr__(name: str):
    """Carica un export pubblico al primo accesso, preservando l'API esistente."""
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
