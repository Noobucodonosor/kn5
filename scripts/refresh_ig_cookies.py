#!/usr/bin/env python3
"""
Esporta i cookie di Instagram dal browser (Chrome loggato su IG) in un file
Netscape `cookies.txt`, usato come auth in ambienti senza browser (Cloud Run).

Uso:
    python scripts/refresh_ig_cookies.py            # -> .secrets/instagram_cookies.txt
    python scripts/refresh_ig_cookies.py out.txt    # path custom

I cookie di Instagram scadono: rilancia questo script quando i download dal
cloud tornano a fallire (403 / "empty media response"), poi ricarica il secret
(vedi deploy/README.md). Richiede `pip install browser_cookie3` e Chrome
loggato su Instagram sulla macchina locale.
"""
import http.cookiejar
import sys
import time
from pathlib import Path


def export_cookies(out_path: str = ".secrets/instagram_cookies.txt",
                   browser: str = "chrome") -> int:
    import browser_cookie3

    loader = getattr(browser_cookie3, browser, None) or browser_cookie3.load
    cj = loader(domain_name="instagram.com")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    mcj = http.cookiejar.MozillaCookieJar(out_path)
    n = 0
    for c in cj:
        if c.expires is None:  # MozillaCookieJar richiede un expires numerico
            c.expires = int(time.time()) + 3600 * 24 * 180
        mcj.set_cookie(c)
        n += 1
    mcj.save(out_path, ignore_discard=True, ignore_expires=True)
    return n


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else ".secrets/instagram_cookies.txt"
    count = export_cookies(out)
    has_session = "sessionid" in Path(out).read_text()
    print(f"✅ Esportati {count} cookie in {out}")
    if not has_session:
        print("⚠️  ATTENZIONE: manca 'sessionid' — assicurati che Chrome sia "
              "loggato su Instagram, poi rilancia.")
        sys.exit(1)
    print("   sessionid presente: l'auth è valida.")
