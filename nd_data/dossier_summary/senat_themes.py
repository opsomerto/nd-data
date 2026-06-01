"""Read hand-curated Sénat themes from data.senat.fr."""

import csv
from dataclasses import dataclass
from urllib.request import urlopen

from nd_data.dossier_summary.themes import normalize_senat_theme


SENAT_DOSSIERS_CSV_URL = "https://data.senat.fr/data/dosleg/dossiers-legislatifs.csv"


@dataclass
class SenatThemeMatch:
    senat_id: str
    source_url: str
    labels_pretty: list[str]
    labels: list[str]


def senat_id_from_url(senat_url: str | None) -> str | None:
    if not senat_url:
        return None
    return senat_url.rstrip("/").split("/")[-1]


def split_senat_theme_labels(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def load_senat_theme_index(csv_url: str = SENAT_DOSSIERS_CSV_URL) -> dict[str, SenatThemeMatch]:
    """Load Sénat dossier themes keyed by dossier URL filename.

    The CSV can contain several rows per dossier. We keep the first row with non-empty
    themes, matching the logic from the old `compute_dossier_theme.py` script.
    """
    with urlopen(csv_url) as response:
        rows = csv.DictReader(
            (line.decode("latin-1") for line in response.readlines()),
            delimiter=";",
        )
        index: dict[str, SenatThemeMatch] = {}
        for row in rows:
            source_url = row.get("URL du dossier", "")
            senat_id = senat_id_from_url(source_url)
            if not senat_id:
                continue
            labels_pretty = split_senat_theme_labels(row.get("Thèmes"))
            if senat_id in index and index[senat_id].labels_pretty:
                continue
            if not labels_pretty and senat_id in index:
                continue
            index[senat_id] = SenatThemeMatch(
                senat_id=senat_id,
                source_url=source_url,
                labels_pretty=labels_pretty,
                labels=[normalize_senat_theme(label) for label in labels_pretty],
            )
    return index


def find_senat_themes(
    senat_chemin: str | None,
    index: dict[str, SenatThemeMatch],
) -> SenatThemeMatch | None:
    senat_id = senat_id_from_url(senat_chemin)
    if not senat_id:
        return None
    match = index.get(senat_id)
    if match and match.labels:
        return match
    return None
