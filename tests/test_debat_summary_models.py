from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from nd_data.debat_summary.agents import build_discussion_enrichment, discussion_summary_from_llm
from nd_data.debat_summary.models import (
    DebateDynamics,
    DebatDiscussionInputPack,
    DebatDiscussionSummary,
    DiscussionSourceRefs,
    GroupSynthesis,
    LLMCumulativeDebatSynthesis,
    LLMDebatDiscussionSummary,
    LLMGroupSynthesis,
    LLMDebateDynamics,
    LLMSpeakerPosition,
    SpeakerPosition,
    SpeakerStat,
)


def test_speaker_position_rejects_unknown_stance():
    with pytest.raises(ValidationError):
        SpeakerPosition(
            speaker_id="A1",
            nom="Alice Martin",
            position="plutot_contre",
        )


def test_speaker_position_allows_more_than_four_arguments():
    position = SpeakerPosition(
        speaker_id="A1",
        nom="Alice Martin",
        position="favorable",
        arguments=[f"Argument {index}" for index in range(8)],
    )

    assert len(position.arguments) == 8


def test_build_discussion_enrichment_copies_metadata_without_raw_text():
    pack = DebatDiscussionInputPack(
        dossier_uid="D1",
        dossier_titre="Projet de loi test",
        discussion_uid="D1:R1:DEB1:P1",
        debat_uid="DEB1",
        reunion_uid="R1",
        point_odj_uid="P1",
        date_seance=datetime(2026, 1, 5, tzinfo=timezone.utc),
        source_refs=DiscussionSourceRefs(
            dossier_uid="D1",
            debat_uid="DEB1",
            reunion_uid="R1",
            point_odj_uid="P1",
        ),
        intervenants_stats=[
            SpeakerStat(
                speaker_id="A1",
                acteur_uid="A1",
                display_name="Alice Martin",
                acteur={"uid": "A1", "nom": "Alice Martin"},
                groupe_ref={"uid": "G1", "libelle": "SOC"},
                groupe="SOC",
                intervention_count=2,
                word_count=40,
                substantive_word_count=40,
            )
        ],
        groupes_stats=[
            {
                "group_key": "G1",
                "groupe": {"uid": "G1", "libelle": "SOC"},
                "speaker_count": 1,
                "intervention_count": 2,
                "word_count": 40,
                "substantive_word_count": 40,
                "participation": "forte",
                "speaker_ids": ["A1"],
            }
        ],
        input_truncated=True,
    )
    summary = DebatDiscussionSummary(
        resume_court="Le débat porte sur le fond du texte.",
        synthese_groupes=[
            GroupSynthesis(
                groupe="SOC",
                synthese="Le groupe intervient fortement.",
            )
        ],
        positions_intervenants_principaux=[
            SpeakerPosition(
                speaker_id="A1",
                nom="Alice Martin",
                position="favorable",
            )
        ],
        dynamique_debat=DebateDynamics(
            ton_general="Technique et calme.",
            niveau_conflit="faible",
            caractere=["technique"],
        ),
    )

    enrichment = build_discussion_enrichment(summary, pack, model_name="test:model")

    assert enrichment.dossier_uid == "D1"
    assert enrichment.discussion_uid == "D1:R1:DEB1:P1"
    assert enrichment.source_refs.point_odj_uid == "P1"
    assert enrichment.input_truncated
    assert enrichment.model_name == "test:model"
    assert enrichment.groupes_stats[0].groupe.uid == "G1"
    assert enrichment.synthese_groupes[0].stats.word_count == 40
    assert enrichment.synthese_groupes[0].participation == "forte"
    assert enrichment.positions_intervenants_principaux[0].acteur.uid == "A1"


def test_llm_output_schemas_do_not_include_computed_persistence_fields():
    discussion_schema = LLMDebatDiscussionSummary.model_json_schema()
    cumulative_schema = LLMCumulativeDebatSynthesis.model_json_schema()
    schema_text = str(discussion_schema) + str(cumulative_schema)

    assert "processed_at" not in schema_text
    assert "model_name" not in schema_text
    assert "agent_version" not in schema_text
    assert "source_refs" not in schema_text
    assert "intervenants_stats" not in schema_text
    assert "groupes_stats" not in schema_text
    assert "groupe_ref" not in schema_text
    assert "acteur" not in schema_text


def test_discussion_summary_from_llm_maps_to_rich_summary_shape():
    output = LLMDebatDiscussionSummary(
        resume_court="Résumé court.",
        positions_intervenants_principaux=[
            LLMSpeakerPosition(
                speaker_id="A1",
                nom="Alice Martin",
                position="favorable",
            )
        ],
        synthese_groupes=[
            LLMGroupSynthesis(
                groupe="SOC",
                synthese="Le groupe soutient le texte.",
            )
        ],
        dynamique_debat=LLMDebateDynamics(
            ton_general="Calme.",
            niveau_conflit="faible",
        ),
    )

    summary = discussion_summary_from_llm(output)

    assert isinstance(summary.positions_intervenants_principaux[0], SpeakerPosition)
    assert summary.positions_intervenants_principaux[0].acteur is None
    assert isinstance(summary.synthese_groupes[0], GroupSynthesis)
    assert summary.synthese_groupes[0].stats is None


def test_rich_summary_accepts_llm_dynamics_if_it_leaks_through():
    summary = DebatDiscussionSummary(
        resume_court="Résumé court.",
        dynamique_debat=LLMDebateDynamics(
            ton_general="Constructif.",
            niveau_conflit="faible",
            caractere=["technique"],
            moments_marquants=["Un échange notable."],
        ),
    )

    assert isinstance(summary.dynamique_debat, DebateDynamics)
    assert summary.dynamique_debat.ton_general == "Constructif."
    assert summary.dynamique_debat.moments_marquants == ["Un échange notable."]


def test_rich_summary_accepts_llm_dynamics_alias_dict():
    summary = DebatDiscussionSummary(
        resume_court="Résumé court.",
        dynamique_debat={
            "ton": "Conflictuel.",
            "conflit": "fort",
            "types": ["politique"],
            "moments": ["Un rappel au règlement."],
        },
    )

    assert summary.dynamique_debat.ton_general == "Conflictuel."
    assert summary.dynamique_debat.niveau_conflit == "fort"
    assert summary.dynamique_debat.caractere == ["politique"]
    assert summary.dynamique_debat.moments_marquants == ["Un rappel au règlement."]
