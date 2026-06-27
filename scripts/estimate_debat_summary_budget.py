"""Estimate debate-summary volume and LLM costs without calling LLM providers.

Examples:
    uv run scripts/estimate_debat_summary_budget.py --legislature 17 --limit 20
    uv run scripts/estimate_debat_summary_budget.py --uid DLR5L17N52428 --json
    uv run scripts/estimate_debat_summary_budget.py --strategies none,50000,100000,200000
"""

import json
import random
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import dotenv
import typer

from nd_data.debat_summary.config import SETTINGS
from nd_data.debat_summary.estimation import (
    CorpusEstimate,
    DEFAULT_MODEL_PRICES,
    estimate_costs,
    parse_truncation_strategy,
    sort_costs,
    summarize_strategy,
    token_estimate,
)
from nd_data.debat_summary.models import DebatAlignmentDocument
from nd_data.debat_summary.mongo import get_mongo_collection
from nd_data.debat_summary.runner import locate_discussion_packs
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import Dossier


dotenv.load_dotenv()

app = typer.Typer(
    help="Estimate debate-summary corpus size and LLM budget.",
    no_args_is_help=True,
)


def parse_strategies(raw: str) -> list[tuple[str, int | None]]:
    return [parse_truncation_strategy(item) for item in raw.split(",") if item.strip()]


def iter_single_dossier(client: TricoteuseAPIClient, uid: str) -> Iterator[Dossier]:
    dossier = client.get_dossier(uid, include=["pointsOdj", "actesLegislatifs"])
    if dossier:
        yield dossier


def iter_dossiers_for_estimate(
    client: TricoteuseAPIClient,
    uid: str | None,
    chambre: str,
    legislature: int | None,
    per_page: int,
    limit: int | None,
) -> Iterator[Dossier]:
    if uid:
        yield from iter_single_dossier(client, uid)
        return

    seen = 0
    page = 1
    while True:
        batch = client.get_dossiers(
            page=page,
            per_page=per_page,
            chambre=chambre,
            legislature=legislature,
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


def count_dossiers_for_estimate(
    client: TricoteuseAPIClient,
    uid: str | None,
    chambre: str,
    legislature: int | None,
    limit: int | None,
) -> int | None:
    if uid:
        return 1
    total = client.get_dossiers_total(chambre=chambre, legislature=legislature)
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
        or "timed out" in message
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
            typer.echo(f"[RETRY] attempt={attempt}/{max_attempts} after {delay:.1f}s: {exc}")
            time.sleep(delay)


def load_alignment_docs(collection: Any, dossier_uid: str) -> list[DebatAlignmentDocument]:
    docs = collection.find({"sections.matched_dossier_uid": dossier_uid})
    return [DebatAlignmentDocument.model_validate(doc) for doc in docs]


def print_strategy_table(estimate: CorpusEstimate, chars_per_token: float) -> None:
    typer.echo("\nVolume by truncation strategy")
    typer.echo(
        "strategy debates dossiers paragraphs speech_int original_chars sent_chars "
        "sent_% prompt_tokens truncated_debates"
    )
    for strategy in estimate.strategies:
        typer.echo(
            f"{strategy.label} "
            f"{strategy.debates} "
            f"{strategy.dossiers_with_debates} "
            f"{strategy.paragraphs} "
            f"{strategy.speech_interventions} "
            f"{strategy.original_speech_text_chars} "
            f"{strategy.sent_speech_text_chars} "
            f"{strategy.sent_ratio} "
            f"{token_estimate(strategy.total_prompt_chars, chars_per_token)} "
            f"{strategy.truncated_debates}"
        )


def print_cost_table(estimate: CorpusEstimate, limit_rows: int | None) -> None:
    typer.echo("\nEstimated cost by model")
    typer.echo("strategy provider model input_tokens output_tokens input_$ output_$ total_$ note")
    rows = sort_costs(estimate.costs, by="provider")
    if limit_rows:
        rows = rows[:limit_rows]
    for cost in rows:
        typer.echo(
            f"{cost.truncation} "
            f"{cost.provider} "
            f"{cost.model} "
            f"{cost.input_tokens} "
            f"{cost.output_tokens} "
            f"{cost.input_cost_usd:.4f} "
            f"{cost.output_cost_usd:.4f} "
            f"{cost.total_cost_usd:.4f} "
            f"{cost.note}"
        )


@app.command()
def main(
    uid: str | None = typer.Option(None, help="Estimate a single dossier UID."),
    legislature: int | None = typer.Option(
        17,
        help="Dossier legislature filter.",
    ),
    chambre: str = typer.Option("AN", help='Filter API list by chamber, default "AN".'),
    limit: int | None = typer.Option(None, help="Estimate at most N dossiers."),
    per_page: int = typer.Option(25, help="API page size."),
    strategies: str = typer.Option(
        "none,50000,100000,200000",
        help="Comma-separated speech-text caps per discussion. Use none for no truncation.",
    ),
    use_alignment: bool = typer.Option(
        True,
        "--alignment/--no-alignment",
        help="Use stored debate section alignments to select real debate sections.",
    ),
    db: str = typer.Option(SETTINGS.mongo_db, help=f"Mongo DB, default {SETTINGS.mongo_db}."),
    alignment_collection_name: str = typer.Option(
        SETTINGS.mongo_alignment_collection,
        "--alignment-collection",
        help=f"Mongo alignment collection, default {SETTINGS.mongo_alignment_collection}.",
    ),
    chars_per_token: float = typer.Option(
        4.0,
        help="Approximate tokenizer ratio used for cross-provider estimates.",
    ),
    output_tokens_per_discussion: int = typer.Option(
        1500,
        help="Estimated output tokens for each discussion summary.",
    ),
    output_tokens_per_cumulative: int = typer.Option(
        2500,
        help="Estimated output tokens for each dossier cumulative synthesis.",
    ),
    cumulative_summary_input_tokens: int = typer.Option(
        1000,
        help="Estimated tokens per discussion summary sent to the cumulative synthesis.",
    ),
    retry_max_attempts: int = typer.Option(
        SETTINGS.retry_max_attempts,
        help="Maximum attempts for retryable API errors.",
    ),
    retry_base_seconds: float = typer.Option(
        SETTINGS.retry_base_seconds,
        help="Initial retry backoff in seconds.",
    ),
    retry_max_seconds: float = typer.Option(
        SETTINGS.retry_max_seconds,
        help="Maximum retry backoff in seconds.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON instead of tables."),
    output: Path | None = typer.Option(None, help="Optional JSON output file."),
    cost_rows: int | None = typer.Option(None, help="Limit printed cost rows."),
) -> None:
    parsed_strategies = parse_strategies(strategies)
    max_strategy_chars = max(
        (chars for _, chars in parsed_strategies if chars is not None), default=0
    )
    full_cap = (
        max_strategy_chars if all(chars is not None for _, chars in parsed_strategies) else 10**12
    )

    api_client = TricoteuseAPIClient()
    alignment_collection = (
        get_mongo_collection(db, alignment_collection_name) if use_alignment else None
    )

    total = run_with_retries(
        lambda: count_dossiers_for_estimate(api_client, uid, chambre, legislature, limit),
        retry_max_attempts,
        retry_base_seconds,
        retry_max_seconds,
    )
    typer.echo(
        f"Estimating legislature={legislature or 'all'} chambre={chambre} "
        f"dossiers={total if total is not None else 'unknown'} strategies={strategies}"
    )

    packs_by_dossier = {}
    error_dossiers = []
    dossiers_seen = 0
    with typer.progressbar(
        iter_dossiers_for_estimate(api_client, uid, chambre, legislature, per_page, limit),
        length=total,
        label="Counting debate packs",
    ) as progress:
        for dossier in progress:
            if not dossier.uid:
                continue
            dossiers_seen += 1
            alignments = (
                load_alignment_docs(alignment_collection, dossier.uid)
                if alignment_collection is not None
                else None
            )
            if use_alignment and not alignments:
                packs_by_dossier[dossier.uid] = []
                continue
            try:
                packs = run_with_retries(
                    lambda: locate_discussion_packs(
                        api_client,
                        dossier.uid,
                        per_page=per_page,
                        max_intervention_chars=full_cap,
                        alignments=alignments,
                        enrich_actors=False,
                    ),
                    retry_max_attempts,
                    retry_base_seconds,
                    retry_max_seconds,
                )
                packs_by_dossier[dossier.uid] = packs
            except Exception as exc:
                typer.echo(f"[ERR] {dossier.uid}: {exc}")
                packs_by_dossier[dossier.uid] = []
                error_dossiers.append(dossier.uid)

    estimate = CorpusEstimate(dossiers_seen=dossiers_seen)
    estimate.errors = len(error_dossiers)
    estimate.error_dossiers = error_dossiers
    estimate.dossiers_with_debates = sum(1 for packs in packs_by_dossier.values() if packs)
    estimate.debates = sum(len(packs) for packs in packs_by_dossier.values())
    for label, max_chars in parsed_strategies:
        strategy = summarize_strategy(
            packs_by_dossier,
            label,
            max_chars,
            chars_per_token,
            cumulative_summary_input_tokens,
        )
        estimate.strategies.append(strategy)
        estimate.costs.extend(
            estimate_costs(
                strategy,
                DEFAULT_MODEL_PRICES,
                chars_per_token,
                output_tokens_per_discussion,
                output_tokens_per_cumulative,
            )
        )

    payload = estimate.model_dump(mode="json")
    if output:
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"Wrote {output}")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    typer.echo(
        f"\nCorpus: dossiers_seen={estimate.dossiers_seen} "
        f"dossiers_with_debates={estimate.dossiers_with_debates} debates={estimate.debates}"
    )
    print_strategy_table(estimate, chars_per_token)
    print_cost_table(estimate, cost_rows)


if __name__ == "__main__":
    app()
