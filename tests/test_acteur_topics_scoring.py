from datetime import UTC, datetime
from collections import Counter

from nd_data.acteur_topics.models import (
    ActorDossierEvidence,
    EvidenceItem,
    EvidenceKind,
    TopicRef,
)
from nd_data.acteur_topics.scoring import (
    aggregate_theme_profiles,
    dossier_breadth_factor,
    score_actor_dossier,
    theme_idf,
)


def evidence_doc(
    acteur_uid: str,
    dossier_uid: str,
    topics: list[TopicRef],
    evidence: list[EvidenceItem],
) -> ActorDossierEvidence:
    doc = ActorDossierEvidence(
        acteur_uid=acteur_uid,
        dossier_uid=dossier_uid,
        topics=topics,
        action_counts=dict(Counter(item.kind.value for item in evidence)),
        raw_score=0,
        computed_at=datetime.now(UTC),
        run_id="run",
        extractor_version="test",
    )
    doc.raw_score = score_actor_dossier(doc)
    return doc


def test_score_actor_dossier_applies_caps():
    doc = evidence_doc(
        "PA1",
        "D1",
        [],
        [EvidenceItem(kind=EvidenceKind.intervention_debat) for _ in range(20)],
    )

    assert score_actor_dossier(doc) == 30


def test_score_actor_dossier_avoids_principal_and_rapporteur_double_counting():
    evidence = ActorDossierEvidence(
        acteur_uid="PA1",
        dossier_uid="D1",
        action_counts={
            "initiateur_dossier": 1,
            "acteur_principal": 1,
            "auteur_principal_document": 1,
            "rapporteur": 1,
            "rapporteur_acte": 1,
        },
        raw_score=0,
        computed_at=datetime.now(UTC),
        run_id="run",
        extractor_version="test",
    )

    assert score_actor_dossier(evidence) == 40


def test_idf_and_breadth_factor_downweight_common_cases():
    assert theme_idf(100, 1) > theme_idf(100, 80)
    assert dossier_breadth_factor(577, 5) > dossier_breadth_factor(577, 300)


def test_aggregate_theme_profiles_uses_topics_and_refs():
    santé = TopicRef(namespace="senat_theme", key="sante", label="Santé")
    budget = TopicRef(namespace="keyword", key="budget")
    docs = [
        evidence_doc(
            "PA1",
            "D1",
            [santé, budget],
            [EvidenceItem(kind=EvidenceKind.initiateur_dossier)],
        ),
        evidence_doc(
            "PA1",
            "D2",
            [santé],
            [EvidenceItem(kind=EvidenceKind.amendement_depose)],
        ),
        evidence_doc(
            "PA2",
            "D1",
            [santé, budget],
            [EvidenceItem(kind=EvidenceKind.presence_commission)],
        ),
    ]

    profiles = aggregate_theme_profiles(docs, run_id="run")
    pa1 = next(profile for profile in profiles if profile.acteur_uid == "PA1")

    assert pa1.dossier_count == 2
    assert pa1.total_score > 0
    assert [theme.key for theme in pa1.main_senat_themes] == ["sante"]
    assert [theme.key for theme in pa1.main_keywords] == ["budget"]
    assert sum(theme.normalized_score for theme in pa1.main_senat_themes) == 1
    assert sum(theme.normalized_score for theme in pa1.main_keywords) == 1
