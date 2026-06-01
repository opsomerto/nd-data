# Agent résumé des dossiers législatifs

## Objectif

Construire un enrichissement IA modulaire pour aider un utilisateur non spécialiste à comprendre un dossier législatif :

- **qualification** : thèmes Sénat, thèmes ouverts, mots-clés ;
- **résumé structuré** : TLDR, pourquoi, enjeux, ce qui change, acteurs concernés, objectif ;
- **situation dans la navette** : où en est le dossier, avec une lecture compacte des actes législatifs.

Le code vit dans `nd_data/dossier_summary/`. Le script batch est `scripts/enrich_dossier_summary.py`.

## Architecture

```
Tricoteuse Dossier
   │
   ├── sources.build_dossier_input_pack()
   │      ├── documents source : initial, annexes, rapports
   │      ├── texte source : exposeMotifsTexte ou PDF -> Markdown
   │      └── navette.build_navette_facts()
   │
   ├── agents Pydantic AI
   │      ├── qualification_agent
   │      ├── structured_summary_agent
   │      ├── navette_agent
   │      └── combined_agent
   │
   └── DossierSummaryEnrichment -> Mongo
```

### Modules

| Module | Entrée principale | Sortie |
|---|---|---|
| `qualification` | métadonnées + documents source avec texte | `themes_senat`, `themes_ouverts`, `keywords` |
| `structured_summary` | métadonnées + documents source avec texte | `tldr`, `pourquoi`, `enjeux`, `ce_qui_change`, `acteurs_concernes`, `objectif` |
| `navette_situation` | métadonnées + documents source sans texte + `navette_facts` | résumé de situation, étape courante, intensité, timeline |
| `combined` | tout le pack | les trois modules en un seul appel |

Pour `navette_situation`, le texte des documents est retiré du prompt : la situation doit surtout reposer sur les actes législatifs et les métriques de procédure.

## Sources documentaires

Par défaut, on utilise uniquement le document initial :

```python
build_dossier_input_pack(dossier, source_options=["initial"])
```

Sources disponibles :

| Option | Contenu |
|---|---|
| `initial` | `documentDeposeRefUid`, notamment `exposeMotifsTexte` |
| `annex` | étude d'impact, avis du Conseil d'État, annexes assimilées |
| `report` | rapports et avis de commission |
| `adopted_text` | textes adoptés |

La classification est heuristique, à partir des champs Tricoteuse du document (`titrePrincipal`, `typeLibelle`, `sousTypeLibelle`, code d'acte associé, etc.).

### Texte source et limite

Le texte vient d'abord de `Document.exposeMotifsTexte`. Si ce champ est absent et que l'option PDF est activée, le PDF est téléchargé puis converti en Markdown via `pymupdf4llm`.

La limite par document source est configurable :

- défaut : `20_000` caractères ;
- env : `DOSSIER_SUMMARY_MAX_SOURCE_CHARS`;
- CLI : `--max-source-chars`.

Chaque `SourceDocument` stocke :

- `original_text_chars` ;
- `text_chars` ;
- `text_truncated`.

Sur un échantillon des 250 dossiers AN les plus récents avec `exposeMotifsTexte`, les tailles observées étaient :

| Percentile | Taille exposé des motifs |
|---:|---:|
| p50 | 5,529 caractères |
| p75 | 8,391 |
| p90 | 12,982 |
| p95 | 17,025 |
| p99 | 25,985 |
| max | 31,195 |

La limite `20_000` couvre donc largement les cas courants et tronque surtout les dossiers longs.

## Navette facts

`build_navette_facts()` compresse `actesLegislatifs` en un objet plus lisible pour le LLM :

- dernier acte, statut, état courant ;
- nombre d'actes ;
- commissions saisies ;
- nombre de débats en séance ;
- nombre de documents ;
- nombre de lectures détectées ;
- présence CMP / promulgation ;
- timeline courte ;
- UIDs d'actes servant d'indices.

Avec `--deep-navette`, le script ajoute des appels Tricoteuse pour :

- `_count.amendements` ;
- durée approximative des débats en séance via `Agenda.timestampDebut` / `timestampFin`.

La durée est une approximation : une réunion peut couvrir plusieurs points d'ordre du jour, donc ce n'est pas un temps de parole strictement attaché au dossier.

## Thèmes Sénat

Les thèmes Sénat attendus par le LLM sont les libellés normalisés stockés en Mongo, par exemple :

```json
"Police et sécurité" -> "police_et_securite"
```

La table est dans `nd_data/dossier_summary/themes.py`.

Tricoteuse expose dans le schéma :

- `Dossier.theme: str | None` ;
- `Dossier.themes: list[DossierThemes]`.

Mais un échantillon de dossiers récents avec `include=["themes"]` n'a retourné aucun thème rempli, y compris pour des dossiers ayant `senatChemin`. Les endpoints séparés testés (`/themes`, `/dossier-themes`, etc.) retournent `404`. Conclusion actuelle : le schéma le permet, mais la donnée n'est pas disponible dans l'API publique telle qu'utilisée ici.

## Batch CLI

Exemples :

```bash
uv run scripts/enrich_dossier_summary.py --uid DLR5L17N52428 --modules qualification --dry-run
uv run scripts/enrich_dossier_summary.py --limit 10 --modules all --sources initial,annexes
uv run scripts/enrich_dossier_summary.py --modules all --combined --deep-navette
```

Paramètres principaux :

| Paramètre | Env | Défaut |
|---|---|---:|
| `--model` | `DOSSIER_SUMMARY_MODEL` | `mistral:mistral-small-latest` |
| `--db` | `DOSSIER_SUMMARY_MONGO_DB` | `parlement` |
| `--collection` | `DOSSIER_SUMMARY_MONGO_COLLECTION` | `dossiers_enrichis` |
| `--max-source-chars` | `DOSSIER_SUMMARY_MAX_SOURCE_CHARS` | `20000` |
| `--delay-seconds` | `DOSSIER_SUMMARY_BATCH_DELAY_SECONDS` | `0.5` |
| `--retry-max-attempts` | `DOSSIER_SUMMARY_RETRY_MAX_ATTEMPTS` | `5` |
| `--retry-base-seconds` | `DOSSIER_SUMMARY_RETRY_BASE_SECONDS` | `1.5` |
| `--retry-max-seconds` | `DOSSIER_SUMMARY_RETRY_MAX_SECONDS` | `30.0` |
| PDF cache | `DOSSIER_SUMMARY_PDF_CACHE_DIR` | temp dir |

Le retry cible les erreurs de type 429/rate limit, timeout et 5xx, avec backoff exponentiel et jitter.

## Estimation tokens

Hypothèses :

- périmètre : dossiers AN Tricoteuse ;
- nombre de dossiers détectés : `1,970` ;
- échantillon tokens : 250 dossiers AN récents ;
- source : `initial` seulement ;
- cap source : `20_000` caractères ;
- estimation tokens : `caractères / 4` ;
- output estimé :
  - qualification : 120 tokens par dossier ;
  - résumé structuré : 300 ;
  - navette : 250 ;
  - combined : 600.

| Mode | Input moyen / dossier | Total input | Output estimé |
|---|---:|---:|---:|
| Qualification | 2,427 | 4.78M | 0.24M |
| Résumé structuré | 2,100 | 4.14M | 0.59M |
| Situation navette | 978 | 1.93M | 0.49M |
| Tous séparés | 5,504 | 10.84M | 1.32M |
| Combined | 2,776 | 5.47M | 1.18M |

Le mode `combined` divise presque par deux les tokens d'entrée par rapport aux trois appels séparés, car le texte initial n'est envoyé qu'une seule fois.

## Prompt de résumé structuré

Le prompt de `structured_summary` est conçu pour éviter les résumés vagues du type "renforcer / améliorer / favoriser" sans mécanisme concret.

Sortie attendue :

| Champ | Description |
|---|---|
| `tldr` | une phrase expliquant ce que ferait concrètement le texte s'il était adopté |
| `pourquoi` | 2 à 5 phrases sur le problème, les constats et le contexte |
| `enjeux` | 2 à 5 enjeux structurés si le texte s'y prête ; 1 suffit pour un texte ciblé |
| `ce_qui_change` | 2 à 6 mesures concrètes si le texte en contient plusieurs ; 1 suffit pour un texte court |
| `acteurs_concernes` | acteurs touchés et impact concret pour chacun |
| `objectif` | 1 à 3 phrases sur le résultat recherché |

Le prompt demande explicitement :

- de décrire les changements juridiques ou opérationnels réels ;
- de nommer les acteurs concernés ;
- d'expliquer ce qui change lorsqu'un dispositif existant est modifié ;
- de ne pas parler de navette, votes ou procédure parlementaire ;
- d'adapter le raisonnement aux résolutions et rapports, qui ne créent pas forcément d'obligations juridiques.

Exemple de TLDR attendu :

> Le texte impose aux plateformes numériques de publier des indicateurs de modération et renforce les pouvoirs de contrôle de l'Arcom afin d'améliorer la transparence des décisions de retrait de contenu.

## Estimation prix backfill complet

Prix indicatifs au moment de l'estimation, en dollars, pour `1,970` dossiers. Ils doivent être revérifiés avant un gros backfill.

| Model | Qualification | Summary | Navette | All separate | Combined |
|---|---:|---:|---:|---:|---:|
| Mistral Small 4 | $0.55 | $0.59 | $0.34 | $1.48 | $0.90 |
| Mistral Medium 3.5 | $8.94 | $10.64 | $6.58 | $26.16 | $17.07 |
| DeepSeek V4 Flash | $0.74 | $0.74 | $0.41 | $1.89 | $1.10 |
| DeepSeek V4 Pro | $2.29 | $2.31 | $1.27 | $5.86 | $3.41 |
| Claude Haiku 4.5 | $5.96 | $7.09 | $4.39 | $17.44 | $11.38 |
| Claude Sonnet 4.6 | $17.89 | $21.27 | $13.16 | $52.33 | $34.14 |
| Claude Opus 4.8 | $29.81 | $35.46 | $21.94 | $87.21 | $56.89 |
| Gemini 2.5 Flash-Lite | $0.57 | $0.65 | $0.39 | $1.61 | $1.02 |
| Gemini 2.5 Flash | $2.03 | $2.72 | $1.81 | $6.55 | $4.60 |
| Gemini 2.5 Pro | $8.34 | $11.08 | $7.33 | $26.75 | $18.66 |
| Gemini 3 Flash Preview | $3.10 | $3.84 | $2.44 | $9.38 | $6.28 |
| Gemini 3.1 Pro Preview | $12.40 | $15.36 | $9.76 | $37.52 | $25.12 |
| OpenAI GPT-5.4 mini | $4.65 | $5.76 | $3.66 | $14.07 | $9.42 |
| OpenAI GPT-5.4 | $15.50 | $19.21 | $12.20 | $46.91 | $31.40 |
| OpenAI GPT-5.5 | $30.99 | $38.41 | $24.40 | $93.81 | $62.80 |

Sources de prix utilisées :

- Mistral : https://mistral.ai/pricing/
- DeepSeek : https://api-docs.deepseek.com/quick_start/pricing
- Claude : https://platform.claude.com/docs/en/about-claude/pricing
- Gemini : https://ai.google.dev/gemini-api/docs/pricing
- OpenAI : https://openai.com/api/pricing/

## Recommandation

1. Pendant l'itération prompt/schema : lancer les modules séparément sur petits lots (`--modules qualification`, puis `summary`, puis `situation`).
2. Pour benchmark qualité : tester 50 à 100 dossiers avec au moins :
   - `mistral:mistral-small-latest` ;
   - `mistral:mistral-medium-latest` ;
   - un modèle fort externe si configuré, par exemple Gemini Flash/Pro ou OpenAI mini.
3. Pour backfill complet quand les prompts sont stabilisés : utiliser `--combined`, probablement avec Mistral Small ou Gemini Flash-Lite/Flash selon qualité observée.
4. Garder `--deep-navette` pour les lots finaux ou les dossiers actifs/importants, car il ajoute des appels API Tricoteuse.
