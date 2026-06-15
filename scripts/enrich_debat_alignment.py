"""Build real compte-rendu section to dossier alignments.

Examples:
    uv run scripts/enrich_debat_alignment.py --uid DLR5L17N51516 --dry-run
    uv run scripts/enrich_debat_alignment.py --limit 100
"""

from collections.abc import Iterator
from typing import Any

import dotenv
import typer

from nd_data.debat_summary.alignment import ALIGNMENT_VERSION, build_alignment_document
from nd_data.debat_summary.config import SETTINGS
from nd_data.debat_summary.mongo import build_alignment_update, get_mongo_collection
from nd_data.debat_summary.sources import seance_debate_actes
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import Dossier


dotenv.load_dotenv()

app = typer.Typer(
    help="Align real debate sections with legislative dossiers.", no_args_is_help=True
)


def iter_dossiers(
    client: TricoteuseAPIClient,
    uid: str | None,
    chambre: str,
    per_page: int,
    limit: int | None,
) -> Iterator[Dossier]:
    if uid:
        dossier = client.get_dossier(uid, include=["actesLegislatifs"])
        if dossier:
            yield dossier
        return

    seen = 0
    page = 1
    while True:
        batch = client.get_dossiers(
            page=page,
            per_page=per_page,
            chambre=chambre,
            sort="dateDepot.desc",
            include=["actesLegislatifs"],
        )
        if not batch:
            break
        for dossier in batch:
            yield dossier
            seen += 1
            if limit and seen >= limit:
                return
        if len(batch) < per_page:
            break
        page += 1


def count_dossiers(
    client: TricoteuseAPIClient,
    uid: str | None,
    chambre: str,
    limit: int | None,
) -> int | None:
    if uid:
        return 1
    total = client.get_dossiers_total(chambre=chambre)
    if total is None:
        return limit
    return min(total, limit) if limit else total


def needs_processing(existing: dict[str, Any] | None, force: bool) -> bool:
    if force or not existing:
        return True
    return existing.get("algorithm_version") != ALIGNMENT_VERSION


def build_alignment_docs_for_dossier(client: TricoteuseAPIClient, dossier: Dossier):
    docs = []
    seen_debats = set()
    for acte in seance_debate_actes(dossier):
        if not acte.reunionRefUid:
            continue
        reunion = client.get_reunion(acte.reunionRefUid, include=["pointsOdj"])
        if not reunion:
            continue
        debat_uid = reunion.compteRenduRefUid
        if not debat_uid and reunion.compteRenduRef:
            debat_uid = reunion.compteRenduRef[0].uid
        if not debat_uid or debat_uid in seen_debats:
            continue
        seen_debats.add(debat_uid)
        debat = client.get_debat(debat_uid, include=["paragraphes"])
        if not debat or not debat.paragraphes:
            continue
        docs.append(build_alignment_document(client, reunion, debat, seed_dossiers=[dossier]))
    return docs


@app.command()
def main(
    uid: str | None = typer.Option(None, help="Process a single dossier UID."),
    limit: int | None = typer.Option(None, help="Process at most N dossiers."),
    chambre: str = typer.Option("AN", help='Filter API list by chamber, default "AN".'),
    per_page: int = typer.Option(25, help="API page size."),
    force: bool = typer.Option(False, help="Recompute existing alignments."),
    dry_run: bool = typer.Option(False, help="Do not write to Mongo."),
    db: str = typer.Option(SETTINGS.mongo_db, help=f"Mongo DB, default {SETTINGS.mongo_db}."),
    collection_name: str = typer.Option(
        SETTINGS.mongo_alignment_collection,
        "--collection",
        help=f"Mongo collection, default {SETTINGS.mongo_alignment_collection}.",
    ),
) -> None:
    print(f"Alignment version: {ALIGNMENT_VERSION}")
    print(f"Mongo: {db}.{collection_name} | Dry-run: {dry_run} | Force: {force}")

    client = TricoteuseAPIClient()
    collection = None if dry_run else get_mongo_collection(db, collection_name)
    try:
        total = count_dossiers(client, uid, chambre, limit)
    except Exception as exc:
        total = limit
        typer.echo(f"[WARN] Could not fetch dossier count, streaming anyway: {exc}")

    processed = skipped = errors = 0
    dossiers = iter_dossiers(client, uid, chambre, per_page, limit)
    with typer.progressbar(dossiers, length=total, label="Aligning debates") as progress:
        for dossier in progress:
            if not dossier.uid:
                continue
            try:
                docs = build_alignment_docs_for_dossier(client, dossier)
                if not docs:
                    skipped += 1
                    typer.echo(f"[SKIP] {dossier.uid} no séance debate found")
                    continue
                for doc in docs:
                    existing = (
                        collection.find_one({"debat_uid": doc.debat_uid})
                        if collection is not None
                        else None
                    )
                    if not needs_processing(existing, force):
                        skipped += 1
                        typer.echo(f"[SKIP] {doc.debat_uid} already aligned")
                        continue
                    if dry_run:
                        typer.echo(doc.model_dump_json(indent=2))
                    else:
                        collection.update_one(
                            {"debat_uid": doc.debat_uid},
                            build_alignment_update(doc),
                            upsert=True,
                        )
                    processed += 1
                    typer.echo(f"[OK] {doc.debat_uid} sections={len(doc.sections)}")
            except Exception as exc:
                errors += 1
                typer.echo(f"[ERR] {dossier.uid} {dossier.titre!r}: {exc}")

    print(f"Done. processed={processed} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    app()
