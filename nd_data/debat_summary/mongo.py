"""Mongo helpers for debate summary enrichments."""

from typing import Any

from nd_data.debat_summary.models import CumulativeDebatSynthesis, DebatDiscussionEnrichment
from nd_data.dossier_summary.mongo import get_mongo_collection


def build_discussion_update(enrichment: DebatDiscussionEnrichment) -> dict[str, Any]:
    return {
        "$set": enrichment.model_dump(mode="python"),
    }


def build_cumulative_update(synthesis: CumulativeDebatSynthesis) -> dict[str, Any]:
    return {
        "$set": synthesis.model_dump(mode="python"),
    }


__all__ = [
    "build_cumulative_update",
    "build_discussion_update",
    "get_mongo_collection",
]
