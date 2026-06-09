"""Build actor-dossier evidence and actor-theme profiles."""

import random
import time
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from uuid import uuid4

import dotenv
import typer

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - only used if tqdm is absent from the runtime env
    tqdm = None

from nd_data.acteur_topics import aggregate_theme_profiles, collect_dossier_evidence
from nd_data.acteur_topics.comparison import (
    build_tricoteuse_profiles_from_evidence,
    compare_profiles,
    summarize_actor_dossier_diffs,
)
from nd_data.acteur_topics.mongo import (
    ensure_evidence_indexes,
    ensure_profile_indexes,
    get_collection,
    replace_actor_profiles,
    replace_dossier_evidence,
    topic_refs_from_dossier_doc,
)
from nd_data.acteur_topics.models import ActorDossierEvidence
from nd_data.dossier_summary.config import SETTINGS
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import Dossier


dotenv.load_dotenv()

app = typer.Typer(
    help="Enrich actors with dossier evidence and topic profiles.", no_args_is_help=True
)

DEFAULT_DB = SETTINGS.mongo_db
DEFAULT_DOSSIER_COLLECTION = SETTINGS.mongo_collection
DEFAULT_EVIDENCE_COLLECTION = "acteur_dossier_evidences"
DEFAULT_PROFILE_COLLECTION = "acteur_theme_profiles"
DEFAULT_COMPARISON_COLLECTION = "acteur_theme_profile_comparisons"
DEFAULT_COLLECT_WORKERS = 3
DEFAULT_EVIDENCE_PER_PAGE = 25


def is_retryable_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    message = str(exc).lower()
    return (
        status_code == 429
        or status_code in {500, 502, 503, 504}
        or "429" in message
        or "peer closed connection" in message
        or "incomplete chunked read" in message
        or "remoteprotocolerror" in message
        or "rate limit" in message
        or "too many requests" in message
        or "timeout" in message
    )


def retry_delay_seconds(attempt: int, base_seconds: float, max_seconds: float) -> float:
    delay = min(max_seconds, base_seconds * (2 ** (attempt - 1)))
    return delay + random.uniform(0, min(1.0, delay / 4))


def run_with_retries(
    operation: Callable, max_attempts: int, base_seconds: float, max_seconds: float
):
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= max_attempts or not is_retryable_error(exc):
                raise
            delay = retry_delay_seconds(attempt, base_seconds, max_seconds)
            print(f"[RETRY] attempt={attempt}/{max_attempts} after {delay:.1f}s: {exc}")
            time.sleep(delay)


def iter_dossiers(
    client: TricoteuseAPIClient,
    uid: str | None,
    chambre: str,
    per_page: int,
    limit: int | None,
) -> Iterator[Dossier]:
    if uid:
        dossier = client.get_dossier(uid)
        if dossier:
            yield dossier
        return

    seen = 0
    page = 1
    while True:
        batch = client.get_dossiers(page=page, per_page=per_page, chambre=chambre)
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


def progress_iter(items, total: int | None, enabled: bool, desc: str = "Collect evidence"):
    if enabled and tqdm is not None:
        return tqdm(items, total=total, unit="dossier", desc=desc)
    if items is None:
        return []
    return items


def progress_write(progress, message: str) -> None:
    if tqdm is not None and hasattr(progress, "write"):
        progress.write(message)
    else:
        print(message)


def make_progress_factory(enabled: bool = True):
    def factory(items, total: int | None, desc: str):
        return progress_iter(items, total=total, enabled=enabled, desc=desc)

    return factory


def load_topics_by_dossier(
    evidence_collection,
    dossier_collection,
    progress_factory=None,
) -> dict[str, list]:
    dossier_uids = evidence_collection.distinct(
        "dossier_uid",
        {"stale": {"$ne": True}},
    )
    iterator = dossier_uids
    if progress_factory is not None:
        iterator = progress_factory(dossier_uids, len(dossier_uids), "Load dossier topics")

    topics_by_dossier = {}
    projection = {
        "uid": 1,
        "senat_theme_enrichment": 1,
        "dossier_summary_enrichment.qualification": 1,
    }
    for dossier_uid in iterator:
        dossier_doc = dossier_collection.find_one({"uid": dossier_uid}, projection)
        if not dossier_doc:
            continue
        topics = topic_refs_from_dossier_doc(dossier_doc)
        if topics:
            topics_by_dossier[dossier_uid] = topics
    return topics_by_dossier


def load_evidence(
    collection,
    topics_by_dossier: dict[str, list] | None = None,
    progress_factory=None,
) -> list[ActorDossierEvidence]:
    total = collection.count_documents({"stale": {"$ne": True}})
    cursor = collection.find({"stale": {"$ne": True}})
    if progress_factory is not None:
        cursor = progress_factory(cursor, total, "Load evidence")
    evidences = []
    for doc in cursor:
        evidence = ActorDossierEvidence.model_validate(doc)
        if topics_by_dossier is not None:
            evidence.topics = topics_by_dossier.get(evidence.dossier_uid, [])
        evidences.append(evidence)
    return evidences


def collect_one_dossier(
    dossier_uid: str,
    run_id: str,
    computed_at: datetime,
    evidence_per_page: int,
) -> list[ActorDossierEvidence]:
    client = TricoteuseAPIClient()
    try:
        return run_with_retries(
            lambda: collect_dossier_evidence(
                client,
                dossier_uid,
                run_id=run_id,
                computed_at=computed_at,
                per_page=evidence_per_page,
            ),
            SETTINGS.retry_max_attempts,
            SETTINGS.retry_base_seconds,
            SETTINGS.retry_max_seconds,
        )
    finally:
        client.close()


def write_collected_dossier(
    collection,
    dossier_uid: str,
    docs: list[ActorDossierEvidence],
    delete_missing: bool,
) -> None:
    if collection is not None:
        replace_dossier_evidence(collection, dossier_uid, docs, delete_missing=delete_missing)


def has_existing_dossier_evidence(collection, dossier_uid: str, resume: bool) -> bool:
    if collection is None or not resume:
        return False
    return (
        collection.find_one(
            {"dossier_uid": dossier_uid, "stale": {"$ne": True}},
            {"_id": 1},
        )
        is not None
    )


def collect_evidence(
    uid: str | None,
    limit: int | None,
    chambre: str,
    per_page: int,
    evidence_per_page: int,
    db: str,
    evidence_collection_name: str,
    delete_missing: bool,
    dry_run: bool,
    workers: int,
    resume: bool,
) -> None:
    client = TricoteuseAPIClient()
    collection = None if dry_run else get_collection(db, evidence_collection_name)
    if collection is not None:
        ensure_evidence_indexes(collection)

    run_id = uuid4().hex
    computed_at = datetime.now(UTC)
    total = count_dossiers(client, uid, chambre, limit)
    processed = 0
    skipped = 0
    progress = progress_iter(
        None,
        total=total,
        enabled=True,
        desc="Collect evidence",
    )
    workers = max(1, workers)
    try:
        if workers == 1:
            for dossier in iter_dossiers(client, uid, chambre, per_page, limit):
                if not dossier.uid:
                    continue
                if has_existing_dossier_evidence(collection, dossier.uid, resume):
                    skipped += 1
                    if hasattr(progress, "set_postfix"):
                        progress.set_postfix(dossier=dossier.uid, skipped=skipped)
                    if hasattr(progress, "update"):
                        progress.update(1)
                    continue
                docs = run_with_retries(
                    lambda dossier_uid=dossier.uid: collect_dossier_evidence(
                        client,
                        dossier_uid,
                        run_id=run_id,
                        computed_at=computed_at,
                        per_page=evidence_per_page,
                    ),
                    SETTINGS.retry_max_attempts,
                    SETTINGS.retry_base_seconds,
                    SETTINGS.retry_max_seconds,
                )
                write_collected_dossier(collection, dossier.uid, docs, delete_missing)
                processed += 1
                if hasattr(progress, "set_postfix"):
                    progress.set_postfix(dossier=dossier.uid, actors=len(docs), workers=workers)
                else:
                    progress_write(
                        progress,
                        f"[EVIDENCE] dossier={dossier.uid} actors={len(docs)}",
                    )
                if hasattr(progress, "update"):
                    progress.update(1)
                if SETTINGS.batch_delay_seconds > 0:
                    time.sleep(SETTINGS.batch_delay_seconds)
            return

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures: dict[Future, str] = {}
            dossier_iter = (
                dossier
                for dossier in iter_dossiers(client, uid, chambre, per_page, limit)
                if dossier.uid
            )

            def submit_until_full() -> None:
                nonlocal skipped
                while len(futures) < workers:
                    try:
                        dossier = next(dossier_iter)
                    except StopIteration:
                        return
                    if has_existing_dossier_evidence(collection, dossier.uid, resume):
                        skipped += 1
                        if hasattr(progress, "set_postfix"):
                            progress.set_postfix(dossier=dossier.uid, skipped=skipped)
                        if hasattr(progress, "update"):
                            progress.update(1)
                        continue
                    future = executor.submit(
                        collect_one_dossier,
                        dossier.uid,
                        run_id,
                        computed_at,
                        evidence_per_page,
                    )
                    futures[future] = dossier.uid

            submit_until_full()
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    dossier_uid = futures.pop(future)
                    docs = future.result()
                    write_collected_dossier(collection, dossier_uid, docs, delete_missing)
                    processed += 1
                    if hasattr(progress, "set_postfix"):
                        progress.set_postfix(dossier=dossier_uid, actors=len(docs), workers=workers)
                    else:
                        progress_write(
                            progress,
                            f"[EVIDENCE] dossier={dossier_uid} actors={len(docs)}",
                        )
                    if hasattr(progress, "update"):
                        progress.update(1)
                    if SETTINGS.batch_delay_seconds > 0:
                        time.sleep(SETTINGS.batch_delay_seconds)
                submit_until_full()
    finally:
        client.close()
        if hasattr(progress, "close"):
            progress.close()
        print(f"[DONE] run_id={run_id} dossiers={processed} skipped={skipped}")


def aggregate_themes(
    db: str,
    dossier_collection_name: str,
    evidence_collection_name: str,
    profile_collection_name: str,
    dry_run: bool,
) -> None:
    evidence_collection = get_collection(db, evidence_collection_name)
    dossier_collection = get_collection(db, dossier_collection_name)
    profile_collection = None if dry_run else get_collection(db, profile_collection_name)
    if profile_collection is not None:
        ensure_profile_indexes(profile_collection)

    progress_factory = make_progress_factory(enabled=True)
    run_id = uuid4().hex
    topics_by_dossier = load_topics_by_dossier(
        evidence_collection,
        dossier_collection,
        progress_factory=progress_factory,
    )
    evidences = load_evidence(
        evidence_collection,
        topics_by_dossier=topics_by_dossier,
        progress_factory=progress_factory,
    )
    profiles = aggregate_theme_profiles(evidences, run_id=run_id)
    print(
        "[AGGREGATE] "
        f"dossier_topics={len(topics_by_dossier)} "
        f"evidences={len(evidences)} "
        f"profiles={len(profiles)}"
    )
    if profile_collection is not None:
        replace_actor_profiles(profile_collection, profiles)
    print(f"[DONE] run_id={run_id}")


def compare_tricoteuse(
    db: str,
    dossier_collection_name: str,
    evidence_collection_name: str,
    comparison_collection_name: str,
    top_n: int,
    dry_run: bool,
) -> None:
    evidence_collection = get_collection(db, evidence_collection_name)
    dossier_collection = get_collection(db, dossier_collection_name)
    comparison_collection = None if dry_run else get_collection(db, comparison_collection_name)
    run_id = uuid4().hex
    computed_at = datetime.now(UTC)
    progress_factory = make_progress_factory(enabled=True)
    topics_by_dossier = load_topics_by_dossier(
        evidence_collection,
        dossier_collection,
        progress_factory=progress_factory,
    )
    evidences = load_evidence(
        evidence_collection,
        topics_by_dossier=topics_by_dossier,
        progress_factory=progress_factory,
    )
    our_profiles = aggregate_theme_profiles(evidences, run_id=run_id, computed_at=computed_at)
    tricoteuse_profiles = build_tricoteuse_profiles_from_evidence(
        evidences,
        run_id=run_id,
        computed_at=computed_at,
    )
    comparisons = compare_profiles(
        our_profiles,
        tricoteuse_profiles,
        run_id=run_id,
        top_n=top_n,
        computed_at=computed_at,
    )
    diffs = summarize_actor_dossier_diffs(evidences)
    for comparison in comparisons:
        comparison["dossier_diffs"] = diffs.get(comparison["acteur_uid"], [])[:50]
        comparison["dossier_diff_count"] = len(diffs.get(comparison["acteur_uid"], []))
    print(
        "[COMPARE] "
        f"our_profiles={len(our_profiles)} "
        f"tricoteuse_profiles={len(tricoteuse_profiles)} "
        f"comparisons={len(comparisons)}"
    )
    if comparison_collection is not None and comparisons:
        from pymongo import ReplaceOne

        comparison_collection.bulk_write(
            [
                ReplaceOne({"acteur_uid": comparison["acteur_uid"]}, comparison, upsert=True)
                for comparison in comparisons
            ],
            ordered=False,
        )
    print(f"[DONE] run_id={run_id}")


@app.command("collect-evidence")
def collect_evidence_command(
    uid: str | None = typer.Option(None, help="Process a single dossier UID."),
    limit: int | None = typer.Option(None, help="Process at most N dossiers."),
    chambre: str = typer.Option("AN", help='Filter API list by chamber, default "AN".'),
    per_page: int = typer.Option(25, help="Dossier API page size."),
    evidence_per_page: int = typer.Option(
        DEFAULT_EVIDENCE_PER_PAGE,
        help=(
            "Evidence source API page size. Lower values are safer for amendment-heavy dossiers."
        ),
    ),
    db: str = typer.Option(DEFAULT_DB, help=f"Mongo DB, default {DEFAULT_DB}."),
    evidence_collection_name: str = typer.Option(
        DEFAULT_EVIDENCE_COLLECTION,
        "--evidence-collection",
        help=f"Evidence collection, default {DEFAULT_EVIDENCE_COLLECTION}.",
    ),
    delete_missing: bool = typer.Option(
        False,
        help="Delete old actor-dossier docs absent from a recomputation instead of marking stale.",
    ),
    workers: int = typer.Option(
        DEFAULT_COLLECT_WORKERS,
        min=1,
        help=(
            "Concurrent Tricoteuses evidence collectors. "
            f"Default {DEFAULT_COLLECT_WORKERS}; use 1 for sequential."
        ),
    ),
    resume: bool = typer.Option(
        False,
        help="Skip dossiers that already have non-stale actor-dossier evidence in Mongo.",
    ),
    dry_run: bool = typer.Option(False, help="Do not write to Mongo."),
) -> None:
    collect_evidence(
        uid=uid,
        limit=limit,
        chambre=chambre,
        per_page=per_page,
        evidence_per_page=evidence_per_page,
        db=db,
        evidence_collection_name=evidence_collection_name,
        delete_missing=delete_missing,
        dry_run=dry_run,
        workers=workers,
        resume=resume,
    )


@app.command("aggregate-themes")
def aggregate_themes_command(
    db: str = typer.Option(DEFAULT_DB, help=f"Mongo DB, default {DEFAULT_DB}."),
    dossier_collection_name: str = typer.Option(
        DEFAULT_DOSSIER_COLLECTION,
        "--dossier-collection",
        help=f"Dossier enrichment collection, default {DEFAULT_DOSSIER_COLLECTION}.",
    ),
    evidence_collection_name: str = typer.Option(
        DEFAULT_EVIDENCE_COLLECTION,
        "--evidence-collection",
        help=f"Evidence collection, default {DEFAULT_EVIDENCE_COLLECTION}.",
    ),
    profile_collection_name: str = typer.Option(
        DEFAULT_PROFILE_COLLECTION,
        "--profile-collection",
        help=f"Profile collection, default {DEFAULT_PROFILE_COLLECTION}.",
    ),
    dry_run: bool = typer.Option(False, help="Do not write profiles."),
) -> None:
    aggregate_themes(
        db=db,
        dossier_collection_name=dossier_collection_name,
        evidence_collection_name=evidence_collection_name,
        profile_collection_name=profile_collection_name,
        dry_run=dry_run,
    )


@app.command("compare-tricoteuse")
def compare_tricoteuse_command(
    db: str = typer.Option(DEFAULT_DB, help=f"Mongo DB, default {DEFAULT_DB}."),
    dossier_collection_name: str = typer.Option(
        DEFAULT_DOSSIER_COLLECTION,
        "--dossier-collection",
        help=f"Dossier enrichment collection, default {DEFAULT_DOSSIER_COLLECTION}.",
    ),
    evidence_collection_name: str = typer.Option(
        DEFAULT_EVIDENCE_COLLECTION,
        "--evidence-collection",
        help=f"Evidence collection, default {DEFAULT_EVIDENCE_COLLECTION}.",
    ),
    comparison_collection_name: str = typer.Option(
        DEFAULT_COMPARISON_COLLECTION,
        "--comparison-collection",
        help=f"Comparison collection, default {DEFAULT_COMPARISON_COLLECTION}.",
    ),
    top_n: int = typer.Option(10, help="Number of top themes to compare per actor."),
    dry_run: bool = typer.Option(False, help="Do not write comparisons."),
) -> None:
    compare_tricoteuse(
        db=db,
        dossier_collection_name=dossier_collection_name,
        evidence_collection_name=evidence_collection_name,
        comparison_collection_name=comparison_collection_name,
        top_n=top_n,
        dry_run=dry_run,
    )


@app.command("run")
def run_command(
    uid: str | None = typer.Option(None, help="Process a single dossier UID."),
    limit: int | None = typer.Option(None, help="Process at most N dossiers."),
    chambre: str = typer.Option("AN", help='Filter API list by chamber, default "AN".'),
    per_page: int = typer.Option(25, help="Dossier API page size."),
    evidence_per_page: int = typer.Option(
        DEFAULT_EVIDENCE_PER_PAGE,
        help=(
            "Evidence source API page size. Lower values are safer for amendment-heavy dossiers."
        ),
    ),
    db: str = typer.Option(DEFAULT_DB, help=f"Mongo DB, default {DEFAULT_DB}."),
    dossier_collection_name: str = typer.Option(
        DEFAULT_DOSSIER_COLLECTION,
        "--dossier-collection",
        help=f"Dossier enrichment collection, default {DEFAULT_DOSSIER_COLLECTION}.",
    ),
    evidence_collection_name: str = typer.Option(
        DEFAULT_EVIDENCE_COLLECTION,
        "--evidence-collection",
        help=f"Evidence collection, default {DEFAULT_EVIDENCE_COLLECTION}.",
    ),
    profile_collection_name: str = typer.Option(
        DEFAULT_PROFILE_COLLECTION,
        "--profile-collection",
        help=f"Profile collection, default {DEFAULT_PROFILE_COLLECTION}.",
    ),
    delete_missing: bool = typer.Option(
        False,
        help="Delete old actor-dossier docs absent from a recomputation instead of marking stale.",
    ),
    workers: int = typer.Option(
        DEFAULT_COLLECT_WORKERS,
        min=1,
        help=(
            "Concurrent Tricoteuses evidence collectors. "
            f"Default {DEFAULT_COLLECT_WORKERS}; use 1 for sequential."
        ),
    ),
    resume: bool = typer.Option(
        False,
        help="Skip dossiers that already have non-stale actor-dossier evidence in Mongo.",
    ),
    dry_run: bool = typer.Option(False, help="Do not write to Mongo."),
) -> None:
    collect_evidence(
        uid=uid,
        limit=limit,
        chambre=chambre,
        per_page=per_page,
        evidence_per_page=evidence_per_page,
        db=db,
        evidence_collection_name=evidence_collection_name,
        delete_missing=delete_missing,
        dry_run=dry_run,
        workers=workers,
        resume=resume,
    )
    if not dry_run:
        aggregate_themes(
            db=db,
            dossier_collection_name=dossier_collection_name,
            evidence_collection_name=evidence_collection_name,
            profile_collection_name=profile_collection_name,
            dry_run=dry_run,
        )


if __name__ == "__main__":
    app()
