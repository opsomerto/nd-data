"""Canonical Sénat theme labels and hand-curated theme loading."""

import csv
import re
import unicodedata
from dataclasses import dataclass
from urllib.request import urlopen


THEMES_SENAT_PRETTY: tuple[str, ...] = (
    "Police et sécurité",
    "Agriculture et pêche",
    "Logement et urbanisme",
    "Fonction publique",
    "Budget",
    "Collectivités territoriales",
    "Pouvoirs publics et Constitution",
    "Transports",
    "Culture",
    "Traités et conventions",
    "Justice",
    "Sécurité sociale",
    "Aménagement du territoire",
    "Environnement",
    "Défense",
    "Éducation",
    "Anciens combattants",
    "Famille",
    "Économie et finances",
    "Sports",
    "Outre-mer",
    "commerce et artisanat",
    "Recherche",
    "Questions sociales et santé",
    "Union européenne",
    "fiscalité",
    "Travail",
    "Énergie",
    "sciences et techniques",
    "PME",
    "Société",
    "Entreprises",
    "Affaires étrangères et coopération",
)

SENAT_DOSSIERS_CSV_URL = "https://data.senat.fr/data/dosleg/dossiers-legislatifs.csv"


@dataclass
class SenatThemeMatch:
    senat_id: str
    source_url: str
    labels_pretty: list[str]
    labels: list[str]


def slugify_theme(label: str) -> str:
    """Normalize a Senat theme label for storage."""
    normalized = unicodedata.normalize("NFKD", label)
    ascii_label = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_label.lower()).strip("_")
    return re.sub(r"_+", "_", slug)


THEME_PRETTY_TO_LABEL: dict[str, str] = {
    pretty: slugify_theme(pretty) for pretty in THEMES_SENAT_PRETTY
}
THEME_LABEL_TO_PRETTY: dict[str, str] = {
    label: pretty for pretty, label in THEME_PRETTY_TO_LABEL.items()
}
THEME_LABELS: tuple[str, ...] = tuple(THEME_PRETTY_TO_LABEL.values())


def normalize_senat_theme(label_or_pretty: str) -> str:
    """Return the canonical storage label for a pretty label or already-normalized label."""
    if label_or_pretty in THEME_LABEL_TO_PRETTY:
        return label_or_pretty
    if label_or_pretty in THEME_PRETTY_TO_LABEL:
        return THEME_PRETTY_TO_LABEL[label_or_pretty]
    return slugify_theme(label_or_pretty)


def pretty_senat_theme(label_or_pretty: str) -> str:
    """Return the display label for a storage label or pretty label."""
    label = normalize_senat_theme(label_or_pretty)
    return THEME_LABEL_TO_PRETTY.get(label, label_or_pretty)


def senat_id_from_url(senat_url: str | None) -> str | None:
    """Extract the Sénat dossier filename used as the stable CSV join key."""
    if not senat_url:
        return None
    return senat_url.rstrip("/").split("/")[-1]


def split_senat_theme_labels(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def load_senat_theme_index(csv_url: str = SENAT_DOSSIERS_CSV_URL) -> dict[str, SenatThemeMatch]:
    """Load Sénat dossier themes keyed by Sénat dossier filename.

    The CSV may contain several rows for the same dossier. We keep the first row with
    non-empty themes, following the useful part of the old theme computation script.
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
