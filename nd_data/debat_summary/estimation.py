"""Estimate debate-summary corpus volume and provider costs."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from nd_data.debat_summary.agents import (
    CUMULATIVE_PROMPT,
    DISCUSSION_PROMPT,
    pack_prompt,
)
from nd_data.debat_summary.models import (
    CumulativeDebatInputPack,
    DebateInputStats,
    DebatDiscussionInputPack,
    InterventionExcerpt,
)


NO_TRUNCATION = "none"


@dataclass(frozen=True)
class ModelPrice:
    provider: str
    model: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    note: str = ""


DEFAULT_MODEL_PRICES = [
    ModelPrice("openai", "gpt-5.5", 5.00, 30.00),
    ModelPrice("openai", "gpt-5.4", 2.50, 15.00),
    ModelPrice("openai", "gpt-5.4-mini", 0.75, 4.50),
    ModelPrice("openai", "gpt-4.1", 2.00, 8.00, "legacy/common"),
    ModelPrice("openai", "gpt-4.1-mini", 0.40, 1.60, "legacy/common"),
    ModelPrice("anthropic", "claude-opus-4.8", 5.00, 25.00),
    ModelPrice("anthropic", "claude-sonnet-4.6", 3.00, 15.00),
    ModelPrice("anthropic", "claude-haiku-4.5", 1.00, 5.00),
    ModelPrice("gemini", "gemini-3.5-flash", 1.50, 9.00),
    ModelPrice("gemini", "gemini-3.1-pro-preview", 2.00, 12.00, "<=200k prompt tier"),
    ModelPrice("gemini", "gemini-3.1-flash-lite", 0.25, 1.50),
    ModelPrice("mistral", "mistral-medium-3.x", 0.40, 2.00, "verify current 3.5 price"),
    ModelPrice("deepseek", "deepseek-v4-flash", 0.14, 0.28, "cache miss input"),
    ModelPrice("deepseek", "deepseek-v4-pro", 0.435, 0.87, "cache miss input"),
]


class TruncationEstimate(BaseModel):
    label: str
    max_intervention_chars: int | None = None
    debates: int = 0
    dossiers_with_debates: int = 0
    paragraphs: int = 0
    speech_interventions: int = 0
    procedure_events: int = 0
    interruptions: int = 0
    original_speech_text_chars: int = 0
    sent_speech_text_chars: int = 0
    discussion_prompt_chars: int = 0
    cumulative_prompt_chars: int = 0
    truncated_debates: int = 0

    @property
    def total_prompt_chars(self) -> int:
        return self.discussion_prompt_chars + self.cumulative_prompt_chars

    @property
    def sent_ratio(self) -> float:
        if self.original_speech_text_chars <= 0:
            return 100.0
        return round((self.sent_speech_text_chars / self.original_speech_text_chars) * 100, 1)


class CostEstimate(BaseModel):
    provider: str
    model: str
    truncation: str
    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    note: str = ""


class CorpusEstimate(BaseModel):
    dossiers_seen: int = 0
    dossiers_with_debates: int = 0
    debates: int = 0
    errors: int = 0
    error_dossiers: list[str] = Field(default_factory=list)
    strategies: list[TruncationEstimate] = Field(default_factory=list)
    costs: list[CostEstimate] = Field(default_factory=list)


def token_estimate(chars: int, chars_per_token: float) -> int:
    if chars <= 0:
        return 0
    return round(chars / chars_per_token)


def money(value: float) -> float:
    return round(value, 4)


def cap_excerpts(
    interventions: list[InterventionExcerpt],
    max_chars: int | None,
) -> tuple[list[InterventionExcerpt], int, bool]:
    if max_chars is None:
        return interventions, sum(len(item.text) for item in interventions), False

    remaining = max(0, max_chars)
    capped = []
    truncated = False
    for intervention in interventions:
        if remaining <= 0:
            truncated = True
            break
        text = intervention.text[:remaining]
        if len(text) < len(intervention.text):
            truncated = True
        capped.append(intervention.model_copy(update={"text": text}))
        remaining -= len(text)

    return capped, sum(len(item.text) for item in capped), truncated


def pack_for_strategy(
    pack: DebatDiscussionInputPack,
    max_chars: int | None,
) -> DebatDiscussionInputPack:
    interventions, input_chars, truncated = cap_excerpts(pack.interventions, max_chars)
    original_chars = pack.input_stats.original_speech_text_chars
    ratio = 100.0 if original_chars <= 0 else round((input_chars / original_chars) * 100, 1)
    input_stats = pack.input_stats.model_copy(
        update={
            "input_speech_text_chars": input_chars,
            "input_sent_ratio": ratio,
            "input_truncated": truncated,
        }
    )
    return pack.model_copy(
        update={
            "interventions": interventions,
            "input_stats": input_stats,
            "input_text_chars": input_chars,
            "input_truncated": truncated,
        }
    )


def prompt_chars_for_discussion(pack: DebatDiscussionInputPack) -> int:
    return len(DISCUSSION_PROMPT.strip()) + len(pack_prompt(pack))


def prompt_chars_for_cumulative(
    dossier_uid: str,
    dossier_titre: str | None,
    discussion_count: int,
    summary_input_tokens: int,
    chars_per_token: float,
) -> int:
    if discussion_count <= 0:
        return 0
    fake_summary_chars = round(summary_input_tokens * chars_per_token)
    pack = CumulativeDebatInputPack(
        dossier_uid=dossier_uid,
        dossier_titre=dossier_titre,
        discussion_summaries=[],
    )
    return (
        len(CUMULATIVE_PROMPT.strip())
        + len(pack_prompt(pack))
        + (discussion_count * fake_summary_chars)
    )


def summarize_strategy(
    packs_by_dossier: dict[str, list[DebatDiscussionInputPack]],
    strategy_label: str,
    max_chars: int | None,
    chars_per_token: float,
    cumulative_summary_input_tokens: int,
) -> TruncationEstimate:
    estimate = TruncationEstimate(label=strategy_label, max_intervention_chars=max_chars)

    for dossier_uid, packs in packs_by_dossier.items():
        if packs:
            estimate.dossiers_with_debates += 1
        dossier_titre = next((pack.dossier_titre for pack in packs if pack.dossier_titre), None)
        estimate.cumulative_prompt_chars += prompt_chars_for_cumulative(
            dossier_uid,
            dossier_titre,
            len(packs),
            cumulative_summary_input_tokens,
            chars_per_token,
        )

        for pack in packs:
            strategy_pack = pack_for_strategy(pack, max_chars)
            stats: DebateInputStats = strategy_pack.input_stats
            estimate.debates += 1
            estimate.paragraphs += stats.paragraph_count
            estimate.speech_interventions += stats.speech_intervention_count
            estimate.procedure_events += stats.procedure_event_count
            estimate.interruptions += stats.interruption_count
            estimate.original_speech_text_chars += stats.original_speech_text_chars
            estimate.sent_speech_text_chars += stats.input_speech_text_chars
            estimate.discussion_prompt_chars += prompt_chars_for_discussion(strategy_pack)
            if stats.input_truncated:
                estimate.truncated_debates += 1

    return estimate


def estimate_costs(
    strategy: TruncationEstimate,
    prices: Iterable[ModelPrice],
    chars_per_token: float,
    output_tokens_per_discussion: int,
    output_tokens_per_cumulative: int,
) -> list[CostEstimate]:
    input_tokens = token_estimate(strategy.total_prompt_chars, chars_per_token)
    output_tokens = (strategy.debates * output_tokens_per_discussion) + (
        strategy.dossiers_with_debates * output_tokens_per_cumulative
    )
    costs = []
    for price in prices:
        input_cost = input_tokens * price.input_usd_per_mtok / 1_000_000
        output_cost = output_tokens * price.output_usd_per_mtok / 1_000_000
        costs.append(
            CostEstimate(
                provider=price.provider,
                model=price.model,
                truncation=strategy.label,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_cost_usd=money(input_cost),
                output_cost_usd=money(output_cost),
                total_cost_usd=money(input_cost + output_cost),
                note=price.note,
            )
        )
    return costs


def parse_truncation_strategy(value: str) -> tuple[str, int | None]:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {NO_TRUNCATION, "no", "notruncation", "no-truncation", "full"}:
        return NO_TRUNCATION, None
    if normalized.endswith("k") and normalized[:-1].isdigit():
        chars = int(normalized[:-1]) * 1000
        return f"{normalized}", chars
    chars = int(normalized)
    return f"{chars // 1000}k" if chars >= 1000 and chars % 1000 == 0 else str(chars), chars


def sort_costs(
    costs: list[CostEstimate],
    by: Literal["provider", "cost"] = "provider",
) -> list[CostEstimate]:
    if by == "cost":
        return sorted(costs, key=lambda item: (item.truncation, item.total_cost_usd, item.model))
    return sorted(costs, key=lambda item: (item.truncation, item.provider, item.total_cost_usd))
