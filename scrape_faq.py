"""
Scraper FAQ La Première Brique (Crisp Helpdesk).

Récupère toutes les catégories, tous les articles, et leur contenu complet,
puis écrit `data/faq_lpb.json`.

Si n'importe quoi plante (réseau, structure HTML changée, article vide…),
le script exit avec un code != 0 — le workflow GitHub Actions ne committera
donc pas et le JSON précédent restera intact.

Usage:
    python scrape_faq.py

Variables d'env optionnelles:
    OUTPUT_PATH  (défaut: data/faq_lpb.json)
    DELAY        (défaut: 1.0 seconde entre chaque requête)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_md
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://lapremierebrique.crisp.help/fr/"
USER_AGENT = "LPB-FAQ-Scraper/1.0 (+https://github.com/lapremierebrique/lpb-faq-scraper)"
TIMEOUT = 20

OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "data/faq_lpb.json")
DELAY = float(os.environ.get("DELAY", "1.0"))

NOISE_MARKERS = (
    "Cet article vous a été utile",
    "Articles en rapport",
    "Démarrer une conversation",
    "Propulsé par Crisp",
    "Powered by Crisp",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("scrape_faq")


def build_session() -> requests.Session:
    """Session avec retry exponentiel sur 429 / 5xx."""
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.5,  # 1.5 → 3 → 6 → 12 → 24s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    return s


def fetch(session: requests.Session, url: str) -> BeautifulSoup:
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def slug_of(url: str, marker: str) -> str:
    parts = url.rstrip("/").split(f"/{marker}/")
    return parts[-1].strip("/") if len(parts) > 1 else url.rstrip("/").rsplit("/", 1)[-1]


def clean_noise(md: str) -> str:
    cutoff = len(md)
    for marker in NOISE_MARKERS:
        idx = md.find(marker)
        if idx > 500:  # only if it appears after real content
            cutoff = min(cutoff, idx)
    return md[:cutoff].rstrip()


def extract_categories(soup: BeautifulSoup) -> list[dict]:
    seen: set[str] = set()
    cats: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/fr/category/" not in href:
            continue
        full = urljoin(BASE_URL, href)
        if full in seen:
            continue
        seen.add(full)
        name = (a.get_text(strip=True) or "").split("\n")[0].strip()
        if not name:
            continue
        cats.append({"name": name, "url": full, "slug": slug_of(full, "category")})
    return cats


def extract_article_links(soup: BeautifulSoup) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/fr/article/" not in href:
            continue
        full = urljoin(BASE_URL, href)
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out


def extract_article(soup: BeautifulSoup, url: str) -> dict:
    title_tag = soup.find("h1") or soup.find("h2")
    title = title_tag.get_text(strip=True) if title_tag else ""

    container = (
        soup.find("article")
        or soup.find(class_="article-content")
        or soup.find(class_="article")
        or soup.find("main")
        or soup.body
    )
    html = str(container) if container else ""
    md = html_to_md(html, heading_style="ATX", bullets="-").strip()
    md = clean_noise(md)

    # Retire un éventuel titre dupliqué en tête
    if title and md.startswith(f"# {title}"):
        md = md[len(f"# {title}"):].lstrip()

    if not title:
        raise RuntimeError(f"Titre introuvable pour {url}")
    if len(md) < 50:
        raise RuntimeError(f"Contenu trop court ({len(md)} chars) pour {url}")

    return {
        "title": title,
        "url": url,
        "slug": slug_of(url, "article"),
        "content_md": md,
        "content_hash": hashlib.sha256(md.encode("utf-8")).hexdigest()[:16],
    }


def main() -> int:
    session = build_session()

    log.info("Fetch home → %s", BASE_URL)
    home = fetch(session, BASE_URL)
    categories = extract_categories(home)
    log.info("  %d catégories", len(categories))

    if len(categories) < 5:
        raise RuntimeError(
            f"Seulement {len(categories)} catégories détectées — "
            f"structure HTML peut-être changée"
        )

    total_articles = 0
    for cat in categories:
        time.sleep(DELAY)
        log.info("Catégorie → %s", cat["name"])
        cat_soup = fetch(session, cat["url"])
        links = extract_article_links(cat_soup)
        log.info("  %d articles", len(links))

        if not links:
            raise RuntimeError(f"Aucun article dans la catégorie {cat['name']}")

        cat["articles"] = []
        for i, art_url in enumerate(links, 1):
            time.sleep(DELAY)
            log.info("  [%d/%d] %s", i, len(links), art_url)
            art_soup = fetch(session, art_url)
            cat["articles"].append(extract_article(art_soup, art_url))
            total_articles += 1

        cat["article_count"] = len(cat["articles"])

    snapshot = {
        "base_url": BASE_URL,
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {
            "category_count": len(categories),
            "article_count": total_articles,
            "total_content_chars": sum(
                len(a["content_md"]) for c in categories for a in c["articles"]
            ),
        },
        "categories": categories,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    log.info(
        "✅ Écrit %s — %d catégories, %d articles (%.1f KB)",
        OUTPUT_PATH, len(categories), total_articles, size_kb,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log.error("❌ Scraping failed: %s", e, exc_info=True)
        sys.exit(1)
