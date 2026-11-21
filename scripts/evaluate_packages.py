#!/usr/bin/env python3
"""Esegue il benchmark offline sui pacchetti KN5 generati."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.skills.evaluation import evaluate_suite


def main() -> int:
    parser = argparse.ArgumentParser(description="Valuta pacchetti KN5 contro i casi golden")
    parser.add_argument("packages", type=Path, help="Cartella contenente una sottocartella per caso")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--json", action="store_true", help="Stampa il report JSON completo")
    args = parser.parse_args()

    report = evaluate_suite(args.cases, args.packages)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for result in report["results"]:
            marker = "PASS" if result["passed"] else "FAIL"
            print(f"[{marker}] {result['id']}")
            for check, passed in result["checks"].items():
                if not passed:
                    print(f"  - {check}")
        for skipped in report["skipped"]:
            print(f"[SKIP] {skipped['id']}: {skipped['reason']}")
        print("Suite:", "PASS" if report["passed"] else "FAIL")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
