"""Batch enrich legislative dossiers with debate-in-session summaries.

Examples:
    uv run scripts/enrich_debat_summary.py --uid DLR5L17N52428 --dry-run
    uv run scripts/enrich_debat_summary.py --limit 10 --max-intervention-chars 30000
    uv run scripts/enrich_debat_summary.py --uid DLR5L17N52428 --no-cumulative --force
"""

import random
import time
from collections.abc import Iterator
from typing import Any

import dotenv
import typer

from nd_data.debat_summary.agents import AGENT_VERSION, DEFAULT_MODEL
from nd_data.debat_summary.config import SETTINGS
from nd_data.debat_summary.mongo import (
    build_cumulative_update,
    build_discussion_update,
    get_mongo_collection,
)
from nd_data.debat_summary.runner import (
    build_cumulative_debat_synthesis,
    enrich_debat_discussion,
    locate_discussion_packs,
)
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import Dossier


dotenv.load_dotenv()

app = typer.Typer(help="Enrich dossiers with debate summary agents.", no_args_is_help=True)


def iter_dossiers(
    client: TricoteuseAPIClient,
    uid: str | None,
    chambre: str,
    per_page: int,
    limit: int | None,
) -> Iterator[Dossier]:
    if uid:
        dossier = client.get_dossier(uid, include=["pointsOdj", "actesLegislatifs"])
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
            include=["pointsOdj", "actesLegislatifs"],
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


def is_retryable_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    message = str(exc).lower()
    return (
        status_code == 429
        or status_code in {500, 502, 503, 504}
        or "429" in message
        or "rate limit" in message
        or "too many requests" in message
        or "timeout" in message
    )


def retry_delay_seconds(attempt: int, base_seconds: float, max_seconds: float) -> float:
    delay = min(max_seconds, base_seconds * (2 ** (attempt - 1)))
    return delay + random.uniform(0, min(1.0, delay / 4))


def run_with_retries(operation, max_attempts: int, base_seconds: float, max_seconds: float):
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= max_attempts or not is_retryable_error(exc):
                raise
            delay = retry_delay_seconds(attempt, base_seconds, max_seconds)
            print(f"[RETRY] attempt={attempt}/{max_attempts} after {delay:.1f}s: {exc}")
            time.sleep(delay)


def existing_summary_needs_processing(
    existing: dict[str, Any] | None,
    model_name: str,
    force: bool,
) -> bool:
    if force or not existing:
        return True
    return (
        existing.get("model_name") != model_name or existing.get("agent_version") != AGENT_VERSION
    )


def wait_between_dossiers(delay_seconds: float) -> None:
    if delay_seconds > 0:
        time.sleep(delay_seconds)


def input_ratio_percent(original_chars: int, input_chars: int) -> float:
    if original_chars <= 0:
        return 100.0
    return round((input_chars / original_chars) * 100, 1)


def log_pack_metrics(pack) -> None:
    percent = input_ratio_percent(pack.original_text_chars, pack.input_text_chars)
    truncated = "truncated" if pack.input_truncated else "full"
    typer.echo(
        "[DEBAT] "
        f"{pack.discussion_uid} interventions={pack.original_intervention_count} "
        f"text_chars={pack.original_text_chars} sent_chars={pack.input_text_chars} "
        f"sent={percent}% {truncated}"
    )


def load_existing_discussion_summaries(collection, dossier_uid: str):
    docs = collection.find({"dossier_uid": dossier_uid})
    from nd_data.debat_summary.models import DebatDiscussionEnrichment

    return [DebatDiscussionEnrichment.model_validate(doc) for doc in docs]


def load_alignment_docs(collection, dossier_uid: str):
    from nd_data.debat_summary.models import DebatAlignmentDocument

    docs = collection.find({"sections.matched_dossier_uid": dossier_uid})
    return [DebatAlignmentDocument.model_validate(doc) for doc in docs]


@app.command()
def main(
    uid: str | None = typer.Option(None, help="Process a single dossier UID."),
    limit: int | None = typer.Option(None, help="Process at most N dossiers."),
    chambre: str = typer.Option("AN", help='Filter API list by chamber, default "AN".'),
    per_page: int = typer.Option(25, help="API page size."),
    force: bool = typer.Option(False, help="Reprocess existing debate summaries."),
    dry_run: bool = typer.Option(False, help="Do not write to Mongo."),
    model: str = typer.Option(DEFAULT_MODEL, help="Pydantic AI model name."),
    max_intervention_chars: int = typer.Option(
        SETTINGS.max_intervention_chars,
        help="Maximum debate text characters sent to the model per discussion.",
    ),
    cumulative: bool = typer.Option(
        True,
        "--cumulative/--no-cumulative",
        help="Build or update the dossier-level debate synthesis.",
    ),
    db: str = typer.Option(SETTINGS.mongo_db, help=f"Mongo DB, default {SETTINGS.mongo_db}."),
    discussion_collection_name: str = typer.Option(
        SETTINGS.mongo_discussion_collection,
        "--discussion-collection",
        help=f"Mongo discussion collection, default {SETTINGS.mongo_discussion_collection}.",
    ),
    cumulative_collection_name: str = typer.Option(
        SETTINGS.mongo_cumulative_collection,
        "--cumulative-collection",
        help=f"Mongo cumulative collection, default {SETTINGS.mongo_cumulative_collection}.",
    ),
    use_alignment: bool = typer.Option(
        True,
        "--alignment/--no-alignment",
        help="Use stored debate section alignments to select real debate sections.",
    ),
    alignment_collection_name: str = typer.Option(
        SETTINGS.mongo_alignment_collection,
        "--alignment-collection",
        help=f"Mongo alignment collection, default {SETTINGS.mongo_alignment_collection}.",
    ),
    delay_seconds: float = typer.Option(
        SETTINGS.batch_delay_seconds,
        help="Sleep between processed dossiers to avoid API/provider throttling.",
    ),
    retry_max_attempts: int = typer.Option(
        SETTINGS.retry_max_attempts,
        help="Maximum attempts for retryable API/provider errors.",
    ),
    retry_base_seconds: float = typer.Option(
        SETTINGS.retry_base_seconds,
        help="Initial retry backoff in seconds.",
    ),
    retry_max_seconds: float = typer.Option(
        SETTINGS.retry_max_seconds,
        help="Maximum retry backoff in seconds.",
    ),
) -> None:
    print(f"Model: {model} | Agent version: {AGENT_VERSION}")
    print(f"Max intervention chars: {max_intervention_chars}")
    print(f"Cumulative synthesis: {cumulative}")
    print(f"Mongo: {db}.{discussion_collection_name} / {cumulative_collection_name}")
    print(f"Alignment: {use_alignment} | Collection: {db}.{alignment_collection_name}")
    print(f"Dry-run: {dry_run} | Force: {force}")

    api_client = TricoteuseAPIClient()
    discussion_collection = (
        None if dry_run else get_mongo_collection(db, discussion_collection_name)
    )
    cumulative_collection = (
        None if dry_run else get_mongo_collection(db, cumulative_collection_name)
    )
    alignment_collection = (
        get_mongo_collection(db, alignment_collection_name) if use_alignment else None
    )

    try:
        total = run_with_retries(
            lambda: count_dossiers(api_client, uid, chambre, limit),
            retry_max_attempts,
            retry_base_seconds,
            retry_max_seconds,
        )
    except Exception as exc:
        total = limit
        typer.echo(f"[WARN] Could not fetch dossier count, streaming anyway: {exc}")
    typer.echo(f"Found dossier count: {total if total is not None else 'unknown'}.")

    processed = skipped = errors = 0
    dossiers = iter_dossiers(api_client, uid, chambre, per_page, limit)
    with typer.progressbar(dossiers, length=total, label="Enriching debate summaries") as progress:
        for dossier in progress:
            if not dossier.uid:
                continue
            try:
                alignments = (
                    load_alignment_docs(alignment_collection, dossier.uid)
                    if alignment_collection is not None
                    else None
                )
                if use_alignment and not alignments:
                    typer.echo(f"[SKIP] {dossier.uid} no debate alignment found")
                    skipped += 1
                    continue
                packs = run_with_retries(
                    lambda: locate_discussion_packs(
                        api_client,
                        dossier.uid,
                        per_page=per_page,
                        max_intervention_chars=max_intervention_chars,
                        alignments=alignments,
                    ),
                    retry_max_attempts,
                    retry_base_seconds,
                    retry_max_seconds,
                )
                if not packs:
                    typer.echo(f"[SKIP] {dossier.uid} no séance debate found")
                    skipped += 1
                    continue
                typer.echo(f"[FOUND] {dossier.uid} debates={len(packs)}")

                new_summaries = []
                with typer.progressbar(
                    packs,
                    length=len(packs),
                    label=f"{dossier.uid} debates",
                ) as debate_progress:
                    for pack in debate_progress:
                        log_pack_metrics(pack)
                        existing = (
                            discussion_collection.find_one({"discussion_uid": pack.discussion_uid})
                            if discussion_collection is not None
                            else None
                        )
                        if not existing_summary_needs_processing(existing, model, force):
                            skipped += 1
                            typer.echo(f"[SKIP] {pack.discussion_uid} already processed")
                            continue

                        enrichment = run_with_retries(
                            lambda: enrich_debat_discussion(pack, model_name=model),
                            retry_max_attempts,
                            retry_base_seconds,
                            retry_max_seconds,
                        )
                        if dry_run:
                            typer.echo(enrichment.model_dump_json(indent=2))
                        else:
                            discussion_collection.update_one(
                                {"discussion_uid": enrichment.discussion_uid},
                                build_discussion_update(enrichment),
                                upsert=True,
                            )
                        new_summaries.append(enrichment)
                        processed += 1
                        typer.echo(f"[OK] {enrichment.discussion_uid}")

                if cumulative:
                    summaries = new_summaries
                    if discussion_collection is not None:
                        summaries = load_existing_discussion_summaries(
                            discussion_collection,
                            dossier.uid,
                        )
                    synthesis = run_with_retries(
                        lambda: build_cumulative_debat_synthesis(
                            dossier.uid,
                            summaries,
                            dossier_titre=dossier.titre,
                            model_name=model,
                        ),
                        retry_max_attempts,
                        retry_base_seconds,
                        retry_max_seconds,
                    )
                    if synthesis is None:
                        typer.echo(f"[SKIP] {dossier.uid} no summaries for cumulative synthesis")
                    elif dry_run:
                        typer.echo(synthesis.model_dump_json(indent=2))
                    else:
                        cumulative_collection.update_one(
                            {"dossier_uid": dossier.uid},
                            build_cumulative_update(synthesis),
                            upsert=True,
                        )
                        typer.echo(f"[OK] cumulative {dossier.uid}")

                wait_between_dossiers(delay_seconds)
            except Exception as exc:
                errors += 1
                typer.echo(f"[ERR] {dossier.uid} {dossier.titre!r}: {exc}")
                wait_between_dossiers(delay_seconds)

    print(f"Done. processed={processed} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    app()
