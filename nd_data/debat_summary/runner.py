"""High-level orchestration for debate summary enrichment."""

from nd_data.debat_summary.agents import (
    DEFAULT_MODEL,
    build_discussion_enrichment,
    run_cumulative_synthesis,
    run_discussion_summary,
)
from nd_data.debat_summary.config import SETTINGS
from nd_data.debat_summary.models import (
    CumulativeDebatInputPack,
    CumulativeDebatSynthesis,
    DebatAlignmentDocument,
    DebatDiscussionEnrichment,
    DebatDiscussionInputPack,
)
from nd_data.debat_summary.sources import locate_debat_discussion_packs as build_located_packs
from nd_data.tricoteuse_api import TricoteuseAPIClient


def enrich_debat_discussion(
    pack: DebatDiscussionInputPack,
    model_name: str = DEFAULT_MODEL,
) -> DebatDiscussionEnrichment:
    summary = run_discussion_summary(pack, model_name=model_name)
    return build_discussion_enrichment(summary, pack, model_name=model_name)


def build_cumulative_debat_synthesis(
    dossier_uid: str,
    summaries: list[DebatDiscussionEnrichment],
    dossier_titre: str | None = None,
    model_name: str = DEFAULT_MODEL,
) -> CumulativeDebatSynthesis | None:
    if not summaries:
        return None
    pack = CumulativeDebatInputPack(
        dossier_uid=dossier_uid,
        dossier_titre=dossier_titre,
        discussion_summaries=sorted(
            summaries,
            key=lambda item: (
                item.date_seance is None,
                item.date_seance,
                item.discussion_uid,
            ),
        ),
    )
    return run_cumulative_synthesis(pack, model_name=model_name)


def locate_discussion_packs(
    client: TricoteuseAPIClient,
    dossier_uid: str,
    per_page: int = 100,
    max_intervention_chars: int = SETTINGS.max_intervention_chars,
    alignments: list[DebatAlignmentDocument] | None = None,
    enrich_actors: bool = True,
) -> list[DebatDiscussionInputPack]:
    return build_located_packs(
        client,
        dossier_uid,
        per_page=per_page,
        max_intervention_chars=max_intervention_chars,
        alignments=alignments,
        enrich_actors=enrich_actors,
    )


locate_debat_discussion_packs = locate_discussion_packs
