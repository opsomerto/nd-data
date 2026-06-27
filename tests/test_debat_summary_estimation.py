from nd_data.debat_summary.estimation import (
    ModelPrice,
    cap_excerpts,
    estimate_costs,
    parse_truncation_strategy,
    token_estimate,
)
from nd_data.debat_summary.models import InterventionExcerpt


def excerpt(text: str) -> InterventionExcerpt:
    return InterventionExcerpt(speaker_id="A1", text=text)


def test_parse_truncation_strategy_accepts_none_and_k_suffix():
    assert parse_truncation_strategy("none") == ("none", None)
    assert parse_truncation_strategy("100k") == ("100k", 100_000)
    assert parse_truncation_strategy("50000") == ("50k", 50_000)


def test_cap_excerpts_keeps_order_and_reports_truncation():
    capped, chars, truncated = cap_excerpts([excerpt("abcd"), excerpt("efgh")], 6)

    assert [item.text for item in capped] == ["abcd", "ef"]
    assert chars == 6
    assert truncated is True


def test_cap_excerpts_none_keeps_all_text():
    capped, chars, truncated = cap_excerpts([excerpt("abcd"), excerpt("efgh")], None)

    assert [item.text for item in capped] == ["abcd", "efgh"]
    assert chars == 8
    assert truncated is False


def test_cost_estimate_uses_input_and_output_prices():
    from nd_data.debat_summary.estimation import TruncationEstimate

    strategy = TruncationEstimate(
        label="none",
        debates=2,
        dossiers_with_debates=1,
        discussion_prompt_chars=4000,
        cumulative_prompt_chars=2000,
    )
    costs = estimate_costs(
        strategy,
        [ModelPrice("provider", "model", input_usd_per_mtok=2, output_usd_per_mtok=10)],
        chars_per_token=4,
        output_tokens_per_discussion=100,
        output_tokens_per_cumulative=300,
    )

    assert token_estimate(strategy.total_prompt_chars, 4) == 1500
    assert costs[0].input_tokens == 1500
    assert costs[0].output_tokens == 500
    assert costs[0].total_cost_usd == 0.008
