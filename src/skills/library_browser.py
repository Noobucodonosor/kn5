"""
LibraryBrowser: legge `library/` per esporlo a una UI web.

Non scrive nulla — è la controparte read-only di [[skill_library_writer]].
Restituisce:
- elenco profili con conteggio pezzi e ultimo aggiornamento;
- elenco pezzi per profilo con titolo, sintesi, data, tag;
- contenuto markdown completo di un pezzo + frontmatter parsato.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class LibraryBrowser:
    def __init__(self, library_root: Union[str, Path] = "library"):
        self.root = Path(library_root)
        self.profiles_dir = self.root / "profiles"
        self.inbox_dir = self.root / "inbox"

    # =================================================================
    # Public API
    # =================================================================

    def list_profiles(self) -> List[Dict]:
        """
        Elenco profili + inbox. Ordinato per ultimo update desc.

        [{name, slug, kind: 'profile'|'inbox', piece_count, last_update, last_title}]
        """
        out: List[Dict] = []

        # Profili veri
        if self.profiles_dir.exists():
            for d in self.profiles_dir.iterdir():
                if not d.is_dir():
                    continue
                info = self._profile_summary(d, kind="profile")
                if info["piece_count"] > 0:
                    out.append(info)

        # Inbox come "profilo speciale" se contiene pezzi
        if self.inbox_dir.exists():
            inbox_pieces = [p for p in self.inbox_dir.glob("*.md") if p.name != "index.md"]
            if inbox_pieces:
                out.append(self._profile_summary(self.inbox_dir, kind="inbox", name="inbox"))

        out.sort(key=lambda x: x["last_update"] or "", reverse=True)
        return out

    def list_pieces(self, profile_slug: str) -> List[Dict]:
        """
        Elenco pezzi di un profilo (o inbox), ordinati per data desc.
        [{filename, title, summary, date, tags, url, like_count, view_count}]
        """
        prof_dir = self._resolve_profile_dir(profile_slug)
        if not prof_dir or not prof_dir.exists():
            return []

        pieces: List[Dict] = []
        for p in prof_dir.glob("*.md"):
            if p.name == "index.md":
                continue
            front, body = _parse_frontmatter(p)
            pieces.append({
                "filename": p.name,
                "title": front.get("title") or _first_h1(body) or p.stem,
                "summary": _extract_summary(body),
                "date": front.get("date"),
                "tags": _as_list(front.get("tags")),
                "url": front.get("url"),
                "media_type": front.get("media_type"),
                "like_count": _as_int(front.get("like_count")),
                "view_count": _as_int(front.get("view_count")),
            })

        pieces.sort(key=lambda x: (x.get("date") or "", x["filename"]), reverse=True)
        return pieces

    def read_piece(self, profile_slug: str, filename: str) -> Optional[Dict]:
        """
        Restituisce un pezzo singolo:
        {filename, profile_slug, frontmatter, body_markdown}
        """
        prof_dir = self._resolve_profile_dir(profile_slug)
        if not prof_dir:
            return None
        # Anti path-traversal
        safe = Path(filename).name
        if safe != filename or not safe.endswith(".md"):
            return None
        path = prof_dir / safe
        if not path.exists() or not path.is_file():
            return None

        front, body = _parse_frontmatter(path)
        return {
            "profile_slug": profile_slug,
            "filename": safe,
            "frontmatter": front,
            "body_markdown": body,
        }

    # =================================================================
    # Internals
    # =================================================================

    def _resolve_profile_dir(self, slug: str) -> Optional[Path]:
        safe = Path(slug).name
        if safe != slug:
            return None
        if safe == "inbox":
            return self.inbox_dir
        return self.profiles_dir / safe

    def _profile_summary(self, dir_path: Path, kind: str, name: Optional[str] = None) -> Dict:
        pieces = sorted(
            (p for p in dir_path.glob("*.md") if p.name != "index.md"),
            key=lambda p: p.name,
            reverse=True,
        )
        last_title = None
        last_update = None
        if pieces:
            last = pieces[0]
            front, body = _parse_frontmatter(last)
            last_title = front.get("title") or _first_h1(body) or last.stem
            last_update = front.get("date") or _iso_mtime(last)
        return {
            "name": name or dir_path.name,
            "slug": dir_path.name,
            "kind": kind,
            "piece_count": len(pieces),
            "last_update": last_update,
            "last_title": last_title,
        }


# =====================================================================
# Frontmatter parsing (best-effort, no PyYAML)
# =====================================================================


_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(path: Path) -> tuple[Dict, str]:
    """
    Estrae frontmatter YAML (a livello superficiale: chiave: valore, liste inline)
    + corpo markdown. Non un parser YAML completo — sufficiente per i file scritti
    da LibraryWriter.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, ""

    m = _FRONT_RE.match(text)
    if not m:
        return {}, text

    front_raw = m.group(1)
    body = text[m.end():]

    data: Dict = {}
    for line in front_raw.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(":")
        if not _:
            continue
        data[key.strip()] = _parse_yaml_scalar(value.strip())
    return data, body


def _parse_yaml_scalar(v: str):
    if v == "" or v.lower() == "null":
        return None
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    # Liste inline [a, b, "c d"]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        items = _split_yaml_list(inner)
        return [_parse_yaml_scalar(x.strip()) for x in items]
    # Stringhe quotate
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1].replace('\\"', '"')
    # Numerico
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return v


def _split_yaml_list(inner: str) -> List[str]:
    """Split a inline YAML list rispettando virgole quotate."""
    out: List[str] = []
    buf: List[str] = []
    in_q = None
    for c in inner:
        if in_q:
            buf.append(c)
            if c == in_q:
                in_q = None
            continue
        if c in ('"', "'"):
            in_q = c
            buf.append(c)
            continue
        if c == ",":
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(c)
    if buf:
        out.append("".join(buf).strip())
    return out


# =====================================================================
# Body helpers
# =====================================================================


def _first_h1(body: str) -> Optional[str]:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return None


def _extract_summary(body: str, max_len: int = 240) -> Optional[str]:
    in_summary = False
    collected: List[str] = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("## Sintesi"):
            in_summary = True
            continue
        if in_summary:
            if s.startswith("## "):
                break
            if s:
                collected.append(s)
    if not collected:
        return None
    text = " ".join(collected)
    return text[:max_len] + ("…" if len(text) > max_len else "")


def _as_list(v) -> List:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _as_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _iso_mtime(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return None
