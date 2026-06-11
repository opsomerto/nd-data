# Agent résumé des débats en séance

## Objectif

Construire un enrichissement IA pour aider à comprendre les débats en séance liés à un dossier législatif.

V1 produit:

- un résumé compact par discussion en séance;
- les intervenants principaux et leurs positions quand elles sont identifiables;
- une synthèse par groupe politique;
- une lecture de la dynamique du débat;
- une synthèse cumulative du dossier à partir des résumés de séance.

Le code vit dans `nd_data/debat_summary/`. Le script batch est `scripts/enrich_debat_summary.py`.

## Localisation des débats

Tricoteuse ne fournit pas une relation directe simple `Dossier -> Débats`.
Pour les débats en séance, le signal principal est l'acte législatif, pas l'ordre du jour,
car `pointsOdj` couvre aussi des réunions de commission.

Le chemin principal est:

1. récupérer le dossier avec `actesLegislatifs`;
2. garder les actes dont `codeActe` contient `DEBATS-SEANCE`;
3. récupérer la réunion via `acte.reunionRefUid`;
4. récupérer le compte rendu via `Agenda.compteRenduRefUid`;
5. charger le `Debat` avec `paragraphes`;
6. filtrer les paragraphes avec `Paragraphe.dossierRefUid == dossier.uid`.

Si `acte.pointOdjUid` existe, le point ODJ sert seulement à enrichir les métadonnées et comme
fallback de filtrage si les paragraphes ne portent pas `dossierRefUid`.
Il ne sert pas à découvrir les débats en séance.

Si ce chemin ne donne aucun résultat, aucun fallback global n'est utilisé.
Le dossier est ignoré par le batch (`no séance debate found`) afin de pouvoir investiguer la
forme exacte des données manquantes.

## Données envoyées au modèle

Chaque `DebatDiscussionInputPack` contient:

- les métadonnées minimales du dossier;
- les métadonnées de séance, réunion, débat et point ODJ;
- les prises de parole filtrées, ordonnées et plafonnées (`codeGrammaire == PAROLE_GENERIQUE`);
- les snapshots d'intervenants enrichis avec acteur, groupe, circonscription et mandat principal quand disponibles;
- des objets de référence `acteur` et `groupe_ref` contenant les identifiants Tricoteuse et les libellés utiles pour créer des liens dans l'application;
- les statistiques de parole des intervenants identifiés dans ces prises de parole;
- des statistiques agrégées par groupe;
- un `sommaire_source` extrait des champs de structure des interventions (`sommaire`, `structure`, `typeDebat`, article) quand ils sont disponibles.
- des `procedure_events` pour les titres, articles, votes, adoptions/rejets/retraits d'amendements, suspensions, etc.;
- des `interruptions` séparées pour aider à qualifier la tension et le ton du débat.

Les statistiques d'intervenants et de groupes ne sont pas calculées sur les lignes procédurales ou les interruptions sans orateur.
Ces lignes restent disponibles comme contexte, mais ne créent pas de faux intervenants.
Le texte des prises de parole envoyé au modèle est plafonné par `DEBAT_SUMMARY_MAX_INTERVENTION_CHARS`.

## Collections Mongo

Par défaut:

- `dossier_debat_summaries`: un document par discussion en séance;
- `dossier_debat_syntheses`: une synthèse cumulative par dossier.

Les documents ne stockent pas le texte brut complet des débats. Ils stockent les références source, les statistiques, les sorties structurées et les métadonnées de traitement.

## CLI

Exemples:

```bash
uv run scripts/enrich_debat_summary.py --uid DLR5L17N52428 --dry-run
uv run scripts/enrich_debat_summary.py --limit 10
uv run scripts/enrich_debat_summary.py --uid DLR5L17N52428 --no-cumulative --force
```

Paramètres principaux:

| Paramètre | Env | Défaut |
|---|---|---:|
| `--model` | `DEBAT_SUMMARY_MODEL` | `DOSSIER_SUMMARY_MODEL` |
| `--db` | `DEBAT_SUMMARY_MONGO_DB` | `parlement` |
| `--discussion-collection` | `DEBAT_SUMMARY_DISCUSSION_COLLECTION` | `dossier_debat_summaries` |
| `--cumulative-collection` | `DEBAT_SUMMARY_CUMULATIVE_COLLECTION` | `dossier_debat_syntheses` |
| `--max-intervention-chars` | `DEBAT_SUMMARY_MAX_INTERVENTION_CHARS` | `40000` |

## Sorties structurées

L'agent utilise deux niveaux de modèles:

- un modèle de sortie LLM compact, avec uniquement les champs générés par le modèle;
- un modèle persistant enrichi, complété côté code avec les identifiants, références source, statistiques, métadonnées de traitement et timestamps.

Cette séparation évite d'envoyer aux fournisseurs comme Gemini un schéma JSON trop complexe.

Le résumé par discussion contient:

- `resume_court`;
- `sommaire_discussion`;
- `sujets`;
- `positions_intervenants_principaux`;
- `synthese_groupes`;
- `dynamique_debat`;
- `intervenants_stats`;
- `groupes_stats`;
- `source_refs`.

Les synthèses par groupe incluent aussi une qualification de participation (`faible`, `moderee`, `forte`, `indetermine`) et, quand le groupe est reconnu, les statistiques agrégées correspondantes.
`source_refs` référence le dossier, le débat, la réunion et le point d'ordre du jour, mais ne stocke pas la liste des UIDs d'interventions.

La synthèse cumulative consomme uniquement les résumés par discussion, pas les interventions brutes.
