"""Canonical Senat theme labels for dossier summary enrichment."""

import re
import unicodedata


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
