from datetime import UTC, datetime

from nd_data.acteur_topics.comparison import (
    build_tricoteuse_profiles_from_evidence,
    compare_profiles,
    participant_action_counts,
)
from nd_data.acteur_topics.models import (
    ActorDossierEvidence,
    TopicRef,
)
from nd_data.acteur_topics.scoring import aggregate_theme_profiles


def test_participant_action_counts_maps_tricoteuse_fields():
    counts = participant_action_counts(
        {
            "initiateurDossier": True,
            "scoreAmendements": 3,
            "scoreInterventions": 0,
            "rapporteur": False,
        }
    )

    assert counts == {"initiateur_dossier": 1, "amendement_depose": 3}


def test_build_tricoteuse_profiles_and_compare():
    evidence = ActorDossierEvidence(
        acteur_uid="PA1",
        dossier_uid="D1",
        topics=[TopicRef(namespace="senat_theme", key="sante")],
        action_counts={"initiateur_dossier": 1},
        raw_score=20,
        computed_at=datetime.now(UTC),
        run_id="run",
        extractor_version="test",
        tricoteuse_participant_snapshot={
            "acteurRefUid": "PA1",
            "dossierRefUid": "D1",
            "initiateurDossier": True,
            "score": 20,
        },
    )

    our_profiles = aggregate_theme_profiles([evidence], run_id="run")
    tricoteuse_profiles = build_tricoteuse_profiles_from_evidence([evidence], run_id="run")
    comparisons = compare_profiles(our_profiles, tricoteuse_profiles, run_id="run")

    assert len(tricoteuse_profiles) == 1
    assert comparisons[0]["overlap_count"] == 1
