"""Compare our actor-theme profiles with Tricoteuses participant-based profiles."""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from nd_data.acteur_topics.models import (
    ActorDossierEvidence,
    ActorThemeProfile,
    EvidenceKind,
)
from nd_data.acteur_topics.scoring import aggregate_theme_profiles

COMPARISON_VERSION = "acteur_topics_comparison_v1"


TRICOTEUSE_FIELD_TO_KIND = {
    "initiateurDossier": EvidenceKind.initiateur_dossier,
    "rapporteur": EvidenceKind.rapporteur,
    "scoreAmendements": EvidenceKind.amendement_depose,
    "scoreCoSignAmendements": EvidenceKind.amendement_cosigne,
    "scoreInterventions": EvidenceKind.intervention_debat,
    "scorePresencesCommission": EvidenceKind.presence_commission,
}


def participant_action_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    counts = {}
    for field, kind in TRICOTEUSE_FIELD_TO_KIND.items():
        value = snapshot.get(field)
        if value is True:
            counts[kind.value] = 1
        elif isinstance(value, int) and value > 0:
            counts[kind.value] = value
    if snapshot.get("auteurDocument"):
        counts["auteur_document"] = 1
    if snapshot.get("coSignataireDocument"):
        counts["cosignataire_document"] = 1
    return counts


def tricoteuse_evidence_from_ours(
    evidence: ActorDossierEvidence,
    computed_at: datetime,
    run_id: str,
) -> ActorDossierEvidence | None:
    snapshot = evidence.tricoteuse_participant_snapshot
    if not snapshot:
        return None
    action_counts = participant_action_counts(snapshot)
    return ActorDossierEvidence(
        acteur_uid=evidence.acteur_uid,
        dossier_uid=evidence.dossier_uid,
        actor=evidence.actor,
        topics=evidence.topics,
        action_counts=action_counts,
        raw_score=float(snapshot.get("score") or 0),
        stale=evidence.stale,
        computed_at=computed_at,
        run_id=run_id,
        extractor_version="tricoteuse_participants_snapshot",
        tricoteuse_participant_snapshot=snapshot,
    )


def theme_rank_map(profile: ActorThemeProfile, limit: int) -> dict[tuple[str, str], int]:
    themes = profile.main_senat_themes + profile.main_open_themes + profile.main_keywords
    return {
        (theme.namespace, theme.key): index for index, theme in enumerate(themes[:limit], start=1)
    }


def top_theme_payload(profile: ActorThemeProfile, limit: int) -> list[dict[str, Any]]:
    themes = profile.main_senat_themes + profile.main_open_themes + profile.main_keywords
    return [theme.model_dump() for theme in themes[:limit]]


def compare_profiles(
    ours: list[ActorThemeProfile],
    tricoteuse: list[ActorThemeProfile],
    run_id: str,
    top_n: int = 10,
    computed_at: datetime | None = None,
) -> list[dict[str, Any]]:
    if computed_at is None:
        computed_at = datetime.now(UTC)
    tricoteuse_by_actor = {profile.acteur_uid: profile for profile in tricoteuse}
    comparisons = []
    for our_profile in ours:
        tricoteuse_profile = tricoteuse_by_actor.get(our_profile.acteur_uid)
        if not tricoteuse_profile:
            continue
        our_ranks = theme_rank_map(our_profile, top_n)
        tricoteuse_ranks = theme_rank_map(tricoteuse_profile, top_n)
        overlap = sorted(set(our_ranks) & set(tricoteuse_ranks))
        comparisons.append(
            {
                "_id": our_profile.acteur_uid,
                "acteur_uid": our_profile.acteur_uid,
                "actor": our_profile.actor.model_dump() if our_profile.actor else None,
                "top_n": top_n,
                "overlap_count": len(overlap),
                "overlap_ratio": len(overlap) / top_n if top_n else 0,
                "our_total_score": our_profile.total_score,
                "tricoteuse_total_score": tricoteuse_profile.total_score,
                "our_top_themes": top_theme_payload(our_profile, top_n),
                "tricoteuse_top_themes": top_theme_payload(tricoteuse_profile, top_n),
                "overlap": [
                    {
                        "namespace": namespace,
                        "key": key,
                        "our_rank": our_ranks[(namespace, key)],
                        "tricoteuse_rank": tricoteuse_ranks[(namespace, key)],
                    }
                    for namespace, key in overlap
                ],
                "computed_at": computed_at,
                "run_id": run_id,
                "comparison_version": COMPARISON_VERSION,
            }
        )
    return comparisons


def build_tricoteuse_profiles_from_evidence(
    evidences: list[ActorDossierEvidence],
    run_id: str,
    computed_at: datetime | None = None,
) -> list[ActorThemeProfile]:
    if computed_at is None:
        computed_at = datetime.now(UTC)
    tricoteuse_evidences = [
        item
        for item in (
            tricoteuse_evidence_from_ours(evidence, computed_at, run_id) for evidence in evidences
        )
        if item is not None
    ]
    return aggregate_theme_profiles(tricoteuse_evidences, run_id=run_id, computed_at=computed_at)


def summarize_actor_dossier_diffs(evidences: list[ActorDossierEvidence]) -> dict[str, Any]:
    diffs_by_actor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for evidence in evidences:
        snapshot = evidence.tricoteuse_participant_snapshot
        if not snapshot:
            continue
        tricoteuse_counts = participant_action_counts(snapshot)
        if evidence.action_counts == tricoteuse_counts:
            continue
        diffs_by_actor[evidence.acteur_uid].append(
            {
                "dossier_uid": evidence.dossier_uid,
                "our_counts": evidence.action_counts,
                "tricoteuse_counts": tricoteuse_counts,
                "our_score": evidence.raw_score,
                "tricoteuse_score": snapshot.get("score"),
            }
        )
    return dict(diffs_by_actor)
