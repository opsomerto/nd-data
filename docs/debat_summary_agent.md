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

Le chemin utilisé pour découvrir les réunions de séance est:

1. récupérer le dossier avec `actesLegislatifs`;
2. garder les actes dont `codeActe` contient `DEBATS-SEANCE`;
3. récupérer la réunion via `acte.reunionRefUid`;
4. récupérer le compte rendu via `Agenda.compteRenduRefUid`;
5. charger le `Debat` avec `paragraphes`.

Si `acte.pointOdjUid` existe, le point ODJ sert seulement à enrichir les métadonnées et comme
indice de debug. Il ne sert pas à découvrir les débats en séance.

Si ce chemin ne donne aucun résultat, aucun fallback global n'est utilisé.
Le dossier est ignoré par le batch (`no séance debate found`) afin de pouvoir investiguer la
forme exacte des données manquantes.

## Alignement des sections

L'ordre du jour planifié (`PointOdj.ordrePoint`) peut être désaligné avec les sections réelles
du compte rendu (`Paragraphe.valeurPtsOdj`). On calcule donc un enrichissement séparé,
stocké dans `debat_section_alignments`, avec un document par `debat_uid`.

Chaque document contient:

- `debat_uid`, `reunion_uid`, date et chambre;
- `sections`: une liste des sections réelles du compte rendu;
- pour chaque section: `real_ordre_point`, titre extrait du compte rendu, type, dossier aligné,
  point ODJ planifié, ids metadata présents dans les paragraphes, score, confiance, evidence,
  scores candidats et warnings.

Script:

```bash
uv run scripts/enrich_debat_alignment.py --uid DLR5L17N51516 --dry-run
uv run scripts/enrich_debat_alignment.py --limit 100
```

Le résumé des débats consomme cette collection par défaut. Pour un dossier donné, il récupère
les documents où `sections.matched_dossier_uid == dossier_uid`, puis filtre les paragraphes
avec `Paragraphe.valeurPtsOdj == section.real_ordre_point`.
Ainsi `dossierRefUid`, `pointOdjRefUid` et `PointOdj.ordrePoint` deviennent des signaux de debug,
pas la source de vérité pour sélectionner les interventions.

Le mode `--no-alignment` du script de résumé garde l'ancien comportement direct pour investiguer:
il filtre d'abord par `dossierRefUid`, puis `pointOdjRefUid`, puis `valeurPtsOdj`.

## Données envoyées au modèle

Chaque `DebatDiscussionInputPack` contient:

- les métadonnées minimales du dossier;
- les métadonnées de séance, réunion, débat et point ODJ;
- les prises de parole filtrées, ordonnées et plafonnées (`codeGrammaire == PAROLE_GENERIQUE`);
- les snapshots d'intervenants enrichis avec acteur, groupe, circonscription et mandat principal quand disponibles;
- des objets de référence `acteur` et `groupe_ref` contenant les identifiants Tricoteuse et les libellés utiles pour créer des liens dans l'application;
- les statistiques de parole des intervenants identifiés dans ces prises de parole;
- des statistiques agrégées par groupe;
- `input_stats`: compteurs calculés sur l'entrée envoyée au modèle
  (paragraphes, prises de parole, événements procéduraux, interruptions, caractères disponibles,
  caractères envoyés et ratio de troncature);
- un `sommaire_source` extrait des champs de structure des interventions (`sommaire`, `structure`, `typeDebat`, article) quand ils sont disponibles.
- des `procedure_events` pour les titres, articles, votes, adoptions/rejets/retraits d'amendements, suspensions, etc.;
- des `interruptions` séparées pour aider à qualifier la tension et le ton du débat.

Les statistiques d'intervenants et de groupes ne sont pas calculées sur les lignes procédurales ou les interruptions sans orateur.
Ces lignes restent disponibles comme contexte, mais ne créent pas de faux intervenants.
Les synthèses narratives par groupe ne dupliquent pas les objets de statistiques: `groupes_stats`
est la source de vérité pour comparer la participation des groupes.
Le texte des prises de parole envoyé au modèle est plafonné par `DEBAT_SUMMARY_MAX_INTERVENTION_CHARS`.

## Collections Mongo

Par défaut:

- `debat_section_alignments`: un document par compte rendu / réunion avec l'alignement des sections;
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
| `--alignment-collection` | `DEBAT_ALIGNMENT_COLLECTION` | `debat_section_alignments` |
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
