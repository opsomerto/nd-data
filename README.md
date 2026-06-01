# ND-data

## Python Formatting

This repo uses Ruff for formatting and lightweight linting.

```bash
uv run ruff format .
uv run ruff check --fix .
```

VS Code users should install the recommended Ruff extension; project settings format Python files on save.



## Tricoteuses API Models & Client

Install datamodel-codegen
```
uv tool install "datamodel-code-generator[http]"
```

And create the tricoteuses API models:
```
uv run datamodel-codegen \
    --url https://git.tricoteuses.fr/logiciels/tricoteuses-api-parlement/raw/branch/staging/prisma/openapi/openapi.yaml \
    --input-file-type openapi \
    --output nd_data/tricoteuse_models.py \
    --output-datetime-class datetime \
    --force-optional
```

Once this is done we can use TricoteusesApiClient to query Tricoteuses Parlement API and get typed results.

### Retrieving debates for a dossier

The Tricoteuses model does not expose a direct `Dossier -> Debat` relation. The reliable path is through the agenda / ordre du jour:

1. Start from the dossier UID.
2. Fetch the dossier with `pointsOdj` included, or fetch interventions with `dossierRefUid=<dossier_uid>`.
3. For each `PointOdj`, use:
   - `dossierLegislatifUid` to confirm it belongs to the dossier
   - `agendaRefUid` to retrieve the related reunion
   - `ordrePoint` to isolate the relevant part of the debate
4. Fetch the related `Agenda` / reunion and use `compteRenduRefUid` or `compteRenduRef` to get the `Debat`.
5. Fetch the `Debat` with `paragraphes`, then keep only paragraphs for the dossier:
   - preferably `Paragraphe.dossierRefUid == dossier.uid`
   - or `Paragraphe.pointOdjRefUid == point_odj.uid`
   - or `Paragraphe.valeurPtsOdj == str(point_odj.ordrePoint)` when filtering a full seance debate

API shape:

```http
GET /dossiers/{uid}?include=pointsOdj
GET /reunions/{agendaRefUid}?include=compteRenduRef
GET /debats/{compteRenduRefUid}?include=paragraphes
```



## Database Structure

Mongo:
- DB: "parlement"
- Collections
    - dossiers_enrichis


## Data Enrichments

### Dossier summary / tldr

- Simple qualification du dossier
    - Themes (base senat)
    - Themes (ouvert)
    - Keywords
- Resume structure
    - Pourquoi
    - Enjeu
    - Comment
    - Objectif
- Situtation actuelles dans la navette, interpretation de l'ensemble des acte legistlatif.


### Nom Officieux des dossiers



### Resume des debats

- Resume d'un debats

- Resume de l'ensemble des debats jusqu'a maintenant
