"""Tweakable settings for dossier summary enrichment."""

import os
from pathlib import Path
from tempfile import gettempdir


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


class DossierSummarySettings:
    """Central list of defaults that are likely to need tuning during backfills."""

    model = os.environ.get("DOSSIER_SUMMARY_MODEL", "mistral:mistral-small-latest")
    mongo_db = os.environ.get("DOSSIER_SUMMARY_MONGO_DB", "parlement")
    mongo_collection = os.environ.get(
        "DOSSIER_SUMMARY_MONGO_COLLECTION",
        "dossiers_enrichis",
    )
    max_source_chars = env_int("DOSSIER_SUMMARY_MAX_SOURCE_CHARS", 20_000)
    pdf_cache_dir = Path(
        os.environ.get(
            "DOSSIER_SUMMARY_PDF_CACHE_DIR",
            str(Path(gettempdir()) / "nd_data_dossier_summary_pdf_cache"),
        )
    )
    batch_delay_seconds = env_float("DOSSIER_SUMMARY_BATCH_DELAY_SECONDS", 0.5)
    retry_max_attempts = env_int("DOSSIER_SUMMARY_RETRY_MAX_ATTEMPTS", 5)
    retry_base_seconds = env_float("DOSSIER_SUMMARY_RETRY_BASE_SECONDS", 1.5)
    retry_max_seconds = env_float("DOSSIER_SUMMARY_RETRY_MAX_SECONDS", 30.0)


SETTINGS = DossierSummarySettings()
