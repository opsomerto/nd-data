# ND-data

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

### Retrieving séance debates for a dossier

The Tricoteuses model does not expose a direct `Dossier -> Debat` relation. The reliable path is through the agenda / ordre du jour:

1. Start from the dossier UID.
2. Fetch the dossier with `actesLegislatifs` included.
3. Keep `ActeLegislatif` rows where `codeActe` contains `DEBATS-SEANCE`.
4. For each séance act, fetch the related `Agenda` / reunion via `acte.reunionRefUid`.
5. Use `Agenda.compteRenduRefUid` or `compteRenduRef` to get the `Debat`.
6. Fetch the `Debat` with `paragraphes`, then keep only paragraphs where
   `Paragraphe.dossierRefUid == dossier.uid`.

`PointOdj` is not used to discover séance debates because it also covers commission meetings.
If `acte.pointOdjUid` is available, it can still enrich metadata or provide a fallback filter
when paragraph `dossierRefUid` is missing.
There is intentionally no global fallback to `/interventions?dossierRefUid=...`; if the
acte/reunion/compte-rendu path fails, the batch logs the dossier for investigation.

API shape:

```http
GET /dossiers/{uid}?include=actesLegislatifs
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
