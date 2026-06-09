"""Actor-topic enrichment built from actor-dossier evidence."""

from nd_data.acteur_topics.extractors import collect_dossier_evidence
from nd_data.acteur_topics.models import (
    ActorDossierEvidence,
    ActorThemeProfile,
    EvidenceItem,
    EvidenceKind,
    ThemeScore,
)
from nd_data.acteur_topics.scoring import (
    DEFAULT_WEIGHTS,
    ScoringWeights,
    aggregate_theme_profiles,
    score_actor_dossier,
)

__all__ = [
    "ActorDossierEvidence",
    "ActorThemeProfile",
    "DEFAULT_WEIGHTS",
    "EvidenceItem",
    "EvidenceKind",
    "ScoringWeights",
    "ThemeScore",
    "aggregate_theme_profiles",
    "collect_dossier_evidence",
    "score_actor_dossier",
]
