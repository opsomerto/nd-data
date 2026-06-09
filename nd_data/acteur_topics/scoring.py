"""Scoring and aggregation for actor-topic profiles."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from math import log
from typing import Iterable

from nd_data.acteur_topics.models import (
    ActorDossierEvidence,
    ActorThemeProfile,
    ThemeScore,
    TopicRef,
)

SCORING_VERSION = "acteur_topics_scoring_v1"


@dataclass(frozen=True)
class SignalWeight:
    points: float
    cap: float | None = None


@dataclass(frozen=True)
class ScoringWeights:
    initiateur_dossier: SignalWeight = SignalWeight(20)
    acteur_principal: SignalWeight = SignalWeight(20)
    rapporteur: SignalWeight = SignalWeight(20)
    auteur_principal_document: SignalWeight = SignalWeight(20)
    auteur_document: SignalWeight = SignalWeight(20, 20)
    cosignataire_document: SignalWeight = SignalWeight(5, 20)
    auteur_motion: SignalWeight = SignalWeight(10, 20)
    initiateur_acte: SignalWeight = SignalWeight(10, 20)
    rapporteur_acte: SignalWeight = SignalWeight(20, 20)
    amendement_depose: SignalWeight = SignalWeight(3, 30)
    amendement_cosigne: SignalWeight = SignalWeight(1, 10)
    intervention_debat: SignalWeight = SignalWeight(3, 30)
    presence_commission: SignalWeight = SignalWeight(1, 5)

    def for_kind(self, kind: str) -> SignalWeight:
        return getattr(self, kind)


DEFAULT_WEIGHTS = ScoringWeights()


def score_actor_dossier(
    evidence: ActorDossierEvidence,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> float:
    """Compute the capped weighted score for one actor-dossier evidence doc."""
    score = 0.0
    counts = dict(evidence.action_counts)
    if counts.get("initiateur_dossier"):
        counts["acteur_principal"] = 0
        counts["auteur_principal_document"] = 0
    if counts.get("rapporteur"):
        counts["rapporteur_acte"] = 0
    for kind, count in counts.items():
        if not hasattr(weights, kind):
            continue
        signal = weights.for_kind(kind)
        signal_score = count * signal.points
        if signal.cap is not None:
            signal_score = min(signal_score, signal.cap)
        score += signal_score
    return score


def topic_key(topic: TopicRef) -> tuple[str, str]:
    return (topic.namespace, topic.key)


def theme_idf(total_dossiers_with_topics: int, dossiers_with_theme: int) -> float:
    return log((1 + total_dossiers_with_topics) / (1 + dossiers_with_theme)) + 1


def dossier_breadth_factor(total_active_actors: int, actors_linked_to_dossier: int) -> float:
    if total_active_actors <= 1 or actors_linked_to_dossier <= 1:
        return 1.0
    return log(1 + total_active_actors) / log(1 + actors_linked_to_dossier)


def normalize_within_namespace(themes: list[ThemeScore]) -> None:
    scores_by_namespace: dict[str, float] = defaultdict(float)
    for theme in themes:
        scores_by_namespace[theme.namespace] += theme.score
    for theme in themes:
        namespace_total = scores_by_namespace[theme.namespace]
        theme.normalized_score = theme.score / namespace_total if namespace_total > 0 else 0


def aggregate_theme_profiles(
    evidences: Iterable[ActorDossierEvidence],
    run_id: str,
    computed_at: datetime | None = None,
    max_main_senat_themes: int = 8,
    max_main_open_themes: int = 8,
    max_main_keywords: int = 12,
) -> list[ActorThemeProfile]:
    """Aggregate actor-dossier evidence docs into one topic profile per actor."""
    evidence_list = [item for item in evidences if not item.stale and item.topics]
    if computed_at is None:
        computed_at = datetime.now(UTC)

    dossier_topics: dict[str, set[tuple[str, str]]] = defaultdict(set)
    dossier_actors: dict[str, set[str]] = defaultdict(set)
    all_actors: set[str] = set()
    for evidence in evidence_list:
        all_actors.add(evidence.acteur_uid)
        dossier_actors[evidence.dossier_uid].add(evidence.acteur_uid)
        for topic in evidence.topics:
            dossier_topics[evidence.dossier_uid].add(topic_key(topic))

    total_dossiers_with_topics = len(dossier_topics)
    total_active_actors = len(all_actors)
    dossiers_by_theme: Counter[tuple[str, str]] = Counter()
    for topics in dossier_topics.values():
        dossiers_by_theme.update(topics)

    profiles: dict[str, dict[tuple[str, str], ThemeScore]] = defaultdict(dict)
    actor_snapshots = {}
    actor_dossiers: dict[str, set[str]] = defaultdict(set)

    for evidence in evidence_list:
        raw_score = evidence.raw_score or score_actor_dossier(evidence)
        if raw_score <= 0:
            continue
        actor_snapshots.setdefault(evidence.acteur_uid, evidence.actor)
        actor_dossiers[evidence.acteur_uid].add(evidence.dossier_uid)
        unique_topics = {topic_key(topic): topic for topic in evidence.topics}
        specificity = 1 / len(unique_topics) if unique_topics else 1
        breadth = dossier_breadth_factor(
            total_active_actors,
            len(dossier_actors[evidence.dossier_uid]),
        )

        for key, topic in unique_topics.items():
            contribution = (
                raw_score
                * theme_idf(total_dossiers_with_topics, dossiers_by_theme[key])
                * breadth
                * specificity
            )
            theme_score = profiles[evidence.acteur_uid].setdefault(
                key,
                ThemeScore(namespace=topic.namespace, key=topic.key, label=topic.label),
            )
            theme_score.score += contribution
            theme_score.dossier_count += 1
            for action, count in evidence.action_counts.items():
                theme_score.action_counts[action] = theme_score.action_counts.get(action, 0) + count

    output = []
    for acteur_uid, theme_map in profiles.items():
        themes = sorted(theme_map.values(), key=lambda theme: theme.score, reverse=True)
        total_score = sum(theme.score for theme in themes)
        normalize_within_namespace(themes)
        main_senat_themes = [theme for theme in themes if theme.namespace == "senat_theme"][
            :max_main_senat_themes
        ]
        main_open_themes = [theme for theme in themes if theme.namespace == "open_theme"][
            :max_main_open_themes
        ]
        main_keywords = [theme for theme in themes if theme.namespace == "keyword"][
            :max_main_keywords
        ]
        output.append(
            ActorThemeProfile(
                acteur_uid=acteur_uid,
                actor=actor_snapshots.get(acteur_uid),
                main_senat_themes=main_senat_themes,
                main_open_themes=main_open_themes,
                main_keywords=main_keywords,
                total_score=total_score,
                dossier_count=len(actor_dossiers[acteur_uid]),
                computed_at=computed_at,
                run_id=run_id,
                scoring_version=SCORING_VERSION,
            )
        )
    return sorted(output, key=lambda profile: profile.total_score, reverse=True)
