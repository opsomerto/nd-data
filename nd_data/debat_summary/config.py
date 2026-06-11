"""Tweakable settings for debate summary enrichment."""

import os

from nd_data.dossier_summary.config import SETTINGS as DOSSIER_SETTINGS
from nd_data.dossier_summary.config import env_float, env_int


class DebatSummarySettings:
    """Central defaults for locating and summarizing séance debates."""

    model = os.environ.get("DEBAT_SUMMARY_MODEL", DOSSIER_SETTINGS.model)
    mongo_db = os.environ.get("DEBAT_SUMMARY_MONGO_DB", DOSSIER_SETTINGS.mongo_db)
    mongo_discussion_collection = os.environ.get(
        "DEBAT_SUMMARY_DISCUSSION_COLLECTION",
        "dossier_debat_summaries",
    )
    mongo_cumulative_collection = os.environ.get(
        "DEBAT_SUMMARY_CUMULATIVE_COLLECTION",
        "dossier_debat_syntheses",
    )
    max_intervention_chars = env_int("DEBAT_SUMMARY_MAX_INTERVENTION_CHARS", 40_000)
    batch_delay_seconds = env_float(
        "DEBAT_SUMMARY_BATCH_DELAY_SECONDS",
        DOSSIER_SETTINGS.batch_delay_seconds,
    )
    retry_max_attempts = env_int(
        "DEBAT_SUMMARY_RETRY_MAX_ATTEMPTS",
        DOSSIER_SETTINGS.retry_max_attempts,
    )
    retry_base_seconds = env_float(
        "DEBAT_SUMMARY_RETRY_BASE_SECONDS",
        DOSSIER_SETTINGS.retry_base_seconds,
    )
    retry_max_seconds = env_float(
        "DEBAT_SUMMARY_RETRY_MAX_SECONDS",
        DOSSIER_SETTINGS.retry_max_seconds,
    )


SETTINGS = DebatSummarySettings()
