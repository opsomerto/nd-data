# Actor Theme Pipeline

This pipeline builds actor-topic profiles from Tricoteuses legislative data.

## Steps

1. Collect actor-dossier evidence:

```bash
rtk uv run python scripts/enrich_acteur_topics.py collect-evidence --resume
```

Writes `acteur_dossier_evidences`, one compact document per `(acteur_uid, dossier_uid)`.
It stores action counts only: initiator, rapporteur, document author, amendment author/co-signer,
meaningful debate speech, commission presence, and act-level signals.

2. Aggregate themes:

```bash
rtk uv run python scripts/enrich_acteur_topics.py aggregate-themes
```

Reads dossier topics from `dossiers_enrichis` in memory, joins them by `dossier_uid`,
and writes `acteur_theme_profiles`. Topics are not copied into the evidence collection.

3. Optional comparison with Tricoteuses:

```bash
rtk uv run python scripts/enrich_acteur_topics.py compare-tricoteuse
```

Uses stored `participantsDossiers` snapshots when available to compare our method with theirs.

## Scoring

Each actor-dossier link gets a weighted `raw_score` from action counts. Theme contribution is:

```text
raw_score * theme_idf * dossier_breadth_factor * dossier_specificity
```

- `theme_idf` lowers common themes.
- `dossier_breadth_factor` lowers dossiers linked to many actors.
- `dossier_specificity` splits credit across all topics on a dossier.
- `normalized_score` is computed within each group: Sénat themes, open themes, keywords.

## Output

`acteur_theme_profiles` stores only compact main topics:

- `main_senat_themes`
- `main_open_themes`
- `main_keywords`

Detailed dossier-level evidence remains queryable from `acteur_dossier_evidences`.
