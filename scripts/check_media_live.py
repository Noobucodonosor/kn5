#!/usr/bin/env python3
"""Controllo manuale con rete e cookie per i tipi di media Instagram.

Non fa parte della suite unitaria: scarica davvero contenuti e va eseguito solo
quando si vuole verificare l'integrazione con Instagram.
"""

import json

from src.skills.media_fetcher import MediaFetcher


def main() -> None:
    fetcher = MediaFetcher(temp_dir="temp/test_media")
    urls = {
        "reel": "https://www.instagram.com/reel/DUY8rBwEgvT/",
        "carousel": "https://www.instagram.com/p/DQ4HibODu5C/",
        "image": "https://www.instagram.com/p/DUn6Iz6DSiO/",
    }

    for label, url in urls.items():
        print(f"\n--- Testing {label} ---")
        try:
            result = fetcher.fetch(url)
            print(json.dumps(result, indent=2))
        except Exception as error:
            print(f"Errore su {label}: {error}")


if __name__ == "__main__":
    main()
