"""Batch enrich legislative dossiers with the Pydantic AI dossier summary agents.

Examples:
    uv run scripts/enrich_dossier_summary.py --uid DLR5L17N52428 --modules qualification
    uv run scripts/enrich_dossier_summary.py --limit 10 --modules all --sources initial,annexes
    uv run scripts/enrich_dossier_summary.py --modules all --combined --force
"""

import os
import random
import time
from collections.abc import Iterable
from typing import Any

import dotenv
import typer

from nd_data.dossier_summary import SummaryModule, enrich_dossier_summary
from nd_data.dossier_summary.agents import AGENT_VERSION, DEFAULT_MODEL
from nd_data.dossier_summary.config import SETTINGS
from nd_data.dossier_summary.mongo import get_mongo_collection
from nd_data.dossier_summary.runner import parse_modules
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import Dossier


dotenv.load_dotenv()

DEFAULT_DB = SETTINGS.mongo_db
DEFAULT_COLLECTION = SETTINGS.mongo_collection
app = typer.Typer(help="Enrich dossiers with summary agents.", no_args_is_help=True)


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_module_arg(value: str) -> list[SummaryModule]:
    values = parse_csv(value)
    if values == ["all"] or "all" in values:
        return list(SummaryModule)
    return parse_modules(values)


def parse_source_arg(value: str) -> list[str]:
    aliases = {
        "annexes": "annex",
        "annexe": "annex",
        "reports": "report",
        "rapports": "report",
        "initiale": "initial",
    }
    return [aliases.get(item, item) for item in parse_csv(value)]


def needs_processing(
    existing: dict[str, Any] | None,
    modules: Iterable[SummaryModule],
    model_name: str,
    force: bool,
) -> bool:
    if force or not existing:
        return True
    enrichment = existing.get("dossier_summary_enrichment")
    if not enrichment:
        return True
    if enrichment.get("model_name") != model_name:
        return True
    if enrichment.get("agent_version") != AGENT_VERSION:
        return True
    return any(enrichment.get(module.value) is None for module in modules)


def build_update(enrichment, dossier: Dossier, modules: Iterable[SummaryModule]) -> dict[str, Any]:
    set_doc: dict[str, Any] = {
        "uid": dossier.uid,
        "titre": dossier.titre,
        "chambre": getattr(dossier.chambre, "value", dossier.chambre),
        "legislature": dossier.legislature,
        "type_initiative": dossier.typeInitiative,
        "date_depot": dossier.dateDepot,
        "date_dernier_acte": dossier.dateDernierActe,
        "dossier_summary_enrichment.model_name": enrichment.model_name,
        "dossier_summary_enrichment.agent_version": enrichment.agent_version,
        "dossier_summary_enrichment.source_options": enrichment.source_options,
        "dossier_summary_enrichment.source_document_refs": enrichment.source_document_refs,
        "dossier_summary_enrichment.processed_at": enrichment.processed_at,
    }

    module_values = {
        SummaryModule.qualification: enrichment.qualification,
        SummaryModule.structured_summary: enrichment.structured_summary,
        SummaryModule.navette_situation: enrichment.navette_situation,
    }
    for module in modules:
        value = module_values[module]
        set_doc[f"dossier_summary_enrichment.{module.value}"] = (
            value.model_dump() if value is not None else None
        )

    return {
        "$set": set_doc,
        "$addToSet": {
            "dossier_summary_enrichment.modules_processed": {"$each": [m.value for m in modules]}
        },
    }


def fetch_dossiers(
    client: TricoteuseAPIClient,
    uid: str | None,
    chambre: str,
    per_page: int,
    limit: int | None,
):
    includes = ["actesLegislatifs", "documents", "documentDeposeRef"]
    if uid:
        dossier = client.get_dossier(uid, include=includes)
        return [dossier] if dossier else []

    collected: list[Dossier] = []
    page = 1
    while True:
        batch = client.get_dossiers(
            page=page,
            per_page=per_page,
            chambre=chambre,
            sort="dateDepot.desc",
            include=includes,
        )
        if not batch:
            break
        collected.extend(batch)
        if limit and len(collected) >= limit:
            return collected[:limit]
        if len(batch) < per_page:
            break
        page += 1
    return collected


def is_retryable_error(exc: Exception) -> bool:
    """Retry provider/API throttling and transient server failures."""
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
    """Exponential backoff with small jitter to avoid retry bursts."""
    delay = min(max_seconds, base_seconds * (2 ** (attempt - 1)))
    return delay + random.uniform(0, min(1.0, delay / 4))


def run_with_retries(
    operation,
    max_attempts: int,
    base_seconds: float,
    max_seconds: float,
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


def wait_between_dossiers(delay_seconds: float) -> None:
    if delay_seconds > 0:
        time.sleep(delay_seconds)


@app.command()
def main(
    uid: str | None = typer.Option(None, help="Process a single dossier UID."),
    limit: int | None = typer.Option(None, help="Process at most N dossiers."),
    chambre: str = typer.Option("AN", help='Filter API list by chamber, default "AN".'),
    per_page: int = typer.Option(25, help="API page size."),
    modules: str = typer.Option(
        "all",
        help=(
            "Comma-separated modules: "
            "qualification,summary,structured_summary,situation,navette_situation,all."
        ),
    ),
    sources: str = typer.Option(
        "initial",
        help="Comma-separated source packs: initial,annexes,reports.",
    ),
    model: str = typer.Option(DEFAULT_MODEL, help="Pydantic AI model name."),
    combined: bool = typer.Option(False, help="Use one combined LLM call."),
    force: bool = typer.Option(False, help="Reprocess existing modules."),
    dry_run: bool = typer.Option(False, help="Do not write to Mongo."),
    db: str = typer.Option(DEFAULT_DB, help=f"Mongo DB, default {DEFAULT_DB}."),
    collection_name: str = typer.Option(
        DEFAULT_COLLECTION,
        "--collection",
        help=f"Mongo collection, default {DEFAULT_COLLECTION}.",
    ),
    pdf: bool = typer.Option(
        True,
        "--pdf/--no-pdf",
        help="Enable PDF extraction for optional annex/report documents.",
    ),
    deep_navette: bool = typer.Option(
        False,
        "--deep-navette",
        help="Fetch extra Tricoteuse metrics: amendment count and debate durations.",
    ),
    max_source_chars: int = typer.Option(
        SETTINGS.max_source_chars,
        help="Maximum characters kept per source document.",
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
    parsed_modules = parse_module_arg(modules)
    parsed_sources = parse_source_arg(sources)
    include_pdf_text = pdf and any(source != "initial" for source in parsed_sources)

    print(f"Model: {model} | Agent version: {AGENT_VERSION}")
    print(f"Modules: {[m.value for m in parsed_modules]} | Combined: {combined}")
    print(f"Sources: {parsed_sources} | PDF extraction: {include_pdf_text}")
    print(f"Max source chars: {max_source_chars}")
    print(f"Deep navette metrics: {deep_navette}")
    print(
        "Rate limits: "
        f"delay={delay_seconds}s retries={retry_max_attempts} "
        f"backoff={retry_base_seconds}-{retry_max_seconds}s"
    )
    print(f"Mongo: {db}.{collection_name} | Dry-run: {dry_run}")

    api_client = TricoteuseAPIClient()
    collection = None if dry_run else get_mongo_collection(db, collection_name)
    dossiers = fetch_dossiers(api_client, uid, chambre, per_page, limit)
    print(f"Found {len(dossiers)} dossier(s).")

    processed = skipped = errors = 0
    for dossier in dossiers:
        if not dossier or not dossier.uid:
            continue
        try:
            existing = collection.find_one({"uid": dossier.uid}) if collection is not None else None
            if not needs_processing(existing, parsed_modules, model, force):
                skipped += 1
                print(f"[SKIP] {dossier.uid} already processed")
                continue

            enrichment = run_with_retries(
                lambda: enrich_dossier_summary(
                    dossier,
                    client=api_client,
                    modules=parsed_modules,
                    source_options=parsed_sources,
                    model_name=model,
                    combined=combined,
                    include_pdf_text=include_pdf_text,
                    fetch_debate_durations=deep_navette,
                    fetch_amendment_count=deep_navette,
                    max_source_chars=max_source_chars,
                ),
                max_attempts=retry_max_attempts,
                base_seconds=retry_base_seconds,
                max_seconds=retry_max_seconds,
            )

            if dry_run:
                print(enrichment.model_dump_json(indent=2))
            else:
                collection.update_one(
                    {"uid": dossier.uid},
                    build_update(enrichment, dossier, parsed_modules),
                    upsert=True,
                )
            processed += 1
            print(f"[OK] {dossier.uid} {dossier.titre!r}")
            wait_between_dossiers(delay_seconds)
        except Exception as exc:
            errors += 1
            print(f"[ERR] {dossier.uid} {dossier.titre!r}: {exc}")
            wait_between_dossiers(delay_seconds)

    print(f"Done. processed={processed} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    app()
