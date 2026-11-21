#!/usr/bin/env python3
"""Controlla confini Git, file generati e possibili segreti prima di un commit."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "kn5-site"

FORBIDDEN_ROOT_PREFIXES = (
    ".env",
    ".secrets/",
    ".obsidian/",
    ".claude/",
    "logs/",
    "library/",
    "output/",
    "temp/",
    "venv/",
    "kn5-site/",
)
REQUIRED_ROOT_PATHS = (
    ".env.example",
    "README.md",
    "Makefile",
    "src/skills/content_schema.py",
    "tests/test_content_schema.py",
    "evals/cases.json",
    "deploy/cloudrun.sh",
    "urls.example.txt",
)
SECRET_PATTERNS = (
    re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\b[0-9]{8,}:[A-Za-z0-9_-]{20,}\b"),
)


def git(repo: Path, *args: str, check: bool = True) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def candidate_files(repo: Path) -> list[str]:
    output = git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return [item.decode() for item in output.split(b"\0") if item]


def scan_secrets(repo: Path, files: list[str]) -> list[str]:
    findings = []
    for relative in files:
        path = repo / relative
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            findings.append(relative)
    return findings


def ignored(repo: Path, relative: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", relative],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def main() -> int:
    errors = []
    warnings = []

    root_files = candidate_files(ROOT)
    tracked_root = [item.decode() for item in git(ROOT, "ls-files", "-z").split(b"\0") if item]
    forbidden_tracked = []
    for path in tracked_root:
        if path == ".env.example":
            continue
        if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in FORBIDDEN_ROOT_PREFIXES):
            forbidden_tracked.append(path)
    if forbidden_tracked:
        errors.append("File locali/generati già tracciati nel root repository")

    hidden_required = [path for path in REQUIRED_ROOT_PATHS if ignored(ROOT, path)]
    if hidden_required:
        errors.append("Sorgenti richiesti ignorati: " + ", ".join(hidden_required))

    root_secrets = scan_secrets(ROOT, root_files)
    if root_secrets:
        errors.append(f"Possibili segreti nei candidati root ({len(root_secrets)} file)")

    if not (SITE / ".git").is_dir():
        errors.append("kn5-site non è più riconosciuto come repository indipendente")
    else:
        site_files = candidate_files(SITE)
        site_secrets = scan_secrets(SITE, site_files)
        if site_secrets:
            errors.append(f"Possibili segreti nei candidati Sites ({len(site_secrets)} file)")
        for generated in ("node_modules", ".next", ".vinext", ".wrangler", "dist"):
            if not ignored(SITE, generated):
                errors.append(f"kn5-site/{generated} non è ignorato")
        if git(SITE, "status", "--porcelain").strip():
            warnings.append("Il repository kn5-site contiene modifiche non committate")

    if git(ROOT, "status", "--porcelain").strip():
        warnings.append("Il repository backend contiene modifiche non committate")

    for warning in warnings:
        print(f"[WARN] {warning}")
    for error in errors:
        print(f"[FAIL] {error}")
    if errors:
        return 1
    print(f"[OK] confini repository e scansione segreti ({len(root_files)} file root)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
