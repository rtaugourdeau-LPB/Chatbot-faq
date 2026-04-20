# LPB FAQ Scraper

Scraper hebdomadaire de la FAQ [La Première Brique](https://lapremierebrique.crisp.help/fr/) (hébergée sur Crisp Helpdesk).

Tous les dimanches à 05:00 UTC, une GitHub Action :

1. Fetch la home → extrait les catégories
2. Pour chaque catégorie → extrait la liste des articles
3. Pour chaque article → extrait le contenu complet en Markdown
4. Écrit le tout dans `data/faq_lpb.json`
5. Commit & push **uniquement si** :
   - le script a réussi (exit 0)
   - le JSON a effectivement changé

Si quoi que ce soit plante (réseau, structure HTML modifiée, article vide), le workflow échoue, aucun commit n'est fait, et `data/faq_lpb.json` reste dans son dernier état valide.

## Structure

```
lpb-faq-scraper/
├── .github/workflows/
│   └── weekly_refresh.yml    # cron dimanche 05:00 UTC
├── data/
│   └── faq_lpb.json          # généré par le scraper
├── scrape_faq.py             # le script
├── requirements.txt
├── .gitignore
└── README.md
```

## Run en local

```bash
python -m venv .venv
source .venv/bin/activate    # ou .venv\Scripts\activate sous Windows
pip install -r requirements.txt
python scrape_faq.py
```

Le JSON sort dans `data/faq_lpb.json`. Environ 1 à 3 minutes (dépend du nombre d'articles × `DELAY`).

Variables d'env optionnelles :
- `OUTPUT_PATH` — chemin du JSON (défaut `data/faq_lpb.json`)
- `DELAY` — secondes entre chaque requête (défaut `1.0`)

## Bootstrap du repo (première fois)

```bash
# 1. Créer le repo sur GitHub, puis en local
git init
git add .
git commit -m "feat: initial scraper"
git branch -M main
git remote add origin git@github.com:<user>/lpb-faq-scraper.git
git push -u origin main

# 2. Générer le premier JSON manuellement
#    → va dans l'onglet "Actions" du repo
#    → "Weekly FAQ Refresh" → "Run workflow"
#    (ou run le script en local et commit le JSON)
```

À partir de là, le cron prend le relais tous les dimanches.

## Format du JSON

```json
{
  "base_url": "https://lapremierebrique.crisp.help/fr/",
  "scraped_at": "2026-04-20T05:00:12+00:00",
  "stats": {
    "category_count": 7,
    "article_count": 78,
    "total_content_chars": 145230
  },
  "categories": [
    {
      "name": "Investir",
      "url": "https://lapremierebrique.crisp.help/fr/category/investir-xxx/",
      "slug": "investir-xxx",
      "article_count": 12,
      "articles": [
        {
          "title": "Comment investir ?",
          "url": "https://lapremierebrique.crisp.help/fr/article/...",
          "slug": "...",
          "content_md": "...",
          "content_hash": "a1b2c3d4..."
        }
      ]
    }
  ]
}
```

Le champ `content_hash` (SHA-256 tronqué à 16 chars) permet de détecter rapidement les articles qui ont changé d'une semaine à l'autre sans comparer tout le Markdown.

## Modifier le schedule

Édite la ligne `cron:` dans `.github/workflows/weekly_refresh.yml`. Format classique [cron](https://crontab.guru/).
