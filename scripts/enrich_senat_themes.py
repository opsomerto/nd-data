"""Add hand-curated Sénat themes to the enriched dossier collection."""

from datetime import datetime, timezone
from typing import Literal

import dotenv
import typer
from pymongo import UpdateOne

from nd_data.dossier_summary.config import SETTINGS
from nd_data.dossier_summary.mongo import get_mongo_collection
from nd_data.dossier_summary.themes import find_senat_themes, load_senat_theme_index
from nd_data.tricoteuse_api import TricoteuseAPIClient


dotenv.load_dotenv()

app = typer.Typer(help="Enrich dossiers with hand-curated Sénat themes.", no_args_is_help=True)


def iter_mongo_dossiers(collection, limit: int | None):
    query = {"senat_chemin": {"$nin": [None, ""]}}
    cursor = collection.find(query, {"uid": 1, "titre": 1, "senat_chemin": 1})
    if limit:
        cursor = cursor.limit(limit)
    yield from cursor


def iter_tricoteuse_dossiers(
    client: TricoteuseAPIClient,
    chambre: str,
    per_page: int,
    limit: int | None,
):
    seen = 0
    page = 1
    while True:
        batch = client.get_dossiers(
            page=page,
            per_page=per_page,
            chambre=chambre,
            sort="dateDepot.desc",
        )
        if not batch:
            break
        for dossier in batch:
            if not dossier.senatChemin:
                continue
            yield {
                "uid": dossier.uid,
                "titre": dossier.titre,
                "senat_chemin": dossier.senatChemin,
            }
            seen += 1
            if limit and seen >= limit:
                return
        if len(batch) < per_page:
            break
        page += 1


def build_update(doc: dict, match, force: bool) -> UpdateOne | None:
    existing = doc.get("senat_theme_enrichment")
    if existing and not force:
        return None
    enrichment = {
        "labels": match.labels,
        "labels_pretty": match.labels_pretty,
        "source": "senat",
        "source_url": match.source_url,
        "senat_id": match.senat_id,
        "processed_at": datetime.now(tz=timezone.utc),
    }
    return UpdateOne(
        {"uid": doc["uid"]},
        {
            "$set": {
                "uid": doc["uid"],
                "titre": doc.get("titre"),
                "senat_chemin": doc.get("senat_chemin"),
                "senat_theme_enrichment": enrichment,
            }
        },
        upsert=True,
    )


@app.command()
def main(
    source: Literal["mongo", "tricoteuse"] = typer.Option(
        "mongo",
        help="Read dossiers from Mongo collection or from Tricoteuse API.",
    ),
    db: str = typer.Option(SETTINGS.mongo_db, help="Mongo DB."),
    collection_name: str = typer.Option(
        SETTINGS.mongo_collection,
        "--collection",
        help="Mongo collection.",
    ),
    chambre: str = typer.Option("AN", help="Tricoteuse chamber filter."),
    per_page: int = typer.Option(100, help="Tricoteuse page size."),
    limit: int | None = typer.Option(None, help="Process at most N dossiers."),
    force: bool = typer.Option(False, help="Overwrite existing senat_theme_enrichment."),
    dry_run: bool = typer.Option(False, help="Print changes without writing."),
) -> None:
    collection = (
        None if dry_run and source == "tricoteuse" else get_mongo_collection(db, collection_name)
    )
    theme_index = load_senat_theme_index()
    print(f"Loaded {len(theme_index)} Sénat theme rows.")

    if source == "mongo":
        dossiers = iter_mongo_dossiers(collection, limit)
    else:
        dossiers = iter_tricoteuse_dossiers(TricoteuseAPIClient(), chambre, per_page, limit)

    operations = []
    seen = matched = skipped = 0
    for doc in dossiers:
        seen += 1
        match = find_senat_themes(doc.get("senat_chemin"), theme_index)
        if not match:
            skipped += 1
            continue
        operation = build_update(doc, match, force)
        if operation is None:
            skipped += 1
            continue
        matched += 1
        if dry_run:
            print(doc["uid"], doc.get("senat_chemin"), match.labels_pretty, match.labels)
        else:
            operations.append(operation)

    if operations:
        result = collection.bulk_write(operations)
        print(f"Mongo: upserted={result.upserted_count} modified={result.modified_count}")

    print(f"Done. seen={seen} matched={matched} skipped={skipped} dry_run={dry_run}")


if __name__ == "__main__":
    app()
