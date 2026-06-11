"""Pydantic models for debate summary enrichment."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Position = Literal["favorable", "defavorable", "reserve", "mixte", "indetermine"]
ConflictLevel = Literal["faible", "modere", "fort", "indetermine"]
ParticipationLevel = Literal["faible", "moderee", "forte", "indetermine"]


class ActorRef(BaseModel):
    uid: str | None = None
    nom: str


class GroupRef(BaseModel):
    uid: str | None = None
    libelle: str


class SpeakerSnapshot(BaseModel):
    speaker_id: str
    acteur_uid: str | None = None
    display_name: str
    acteur: ActorRef
    groupe_uid: str | None = None
    groupe: str | None = None
    groupe_ref: GroupRef | None = None
    circonscription: str | None = None
    mandat: str | None = None
    role: str | None = None
    is_president: bool = False
    is_government: bool = False
    is_rapporteur: bool = False


class InterventionExcerpt(BaseModel):
    uid: str | None = None
    speaker_id: str
    ordre: int | None = None
    text: str
    role: str | None = None
    is_president: bool = False


class DebateContextEvent(BaseModel):
    ordre: int | None = None
    code_grammaire: str | None = None
    text: str
    article: str | None = None
    type_debat: str | None = None


class SpeakerStat(BaseModel):
    speaker_id: str
    acteur_uid: str | None = None
    display_name: str
    acteur: ActorRef
    groupe_ref: GroupRef | None = None
    groupe: str | None = None
    intervention_count: int = 0
    word_count: int = 0
    substantive_word_count: int = 0
    is_president: bool = False
    is_government: bool = False
    is_rapporteur: bool = False


class GroupStat(BaseModel):
    group_key: str
    groupe: GroupRef | None = None
    speaker_count: int = 0
    intervention_count: int = 0
    word_count: int = 0
    substantive_word_count: int = 0
    participation: ParticipationLevel = "indetermine"
    speaker_ids: list[str] = Field(default_factory=list)


class DiscussionSourceRefs(BaseModel):
    dossier_uid: str
    debat_uid: str | None = None
    reunion_uid: str | None = None
    point_odj_uid: str | None = None


class DiscussionOutlineItem(BaseModel):
    ordre: int | None = None
    titre: str
    speaker_id: str | None = None
    type_debat: str | None = None
    structure: str | None = None
    article: str | None = None


class DebatDiscussionInputPack(BaseModel):
    dossier_uid: str
    dossier_titre: str | None = None
    chambre: str | None = None
    legislature: int | None = None
    type_initiative: str | None = None
    type_procedure: str | None = None
    statut: str | None = None
    discussion_uid: str
    debat_uid: str | None = None
    reunion_uid: str | None = None
    point_odj_uid: str | None = None
    date_seance: datetime | None = None
    objet: str | None = None
    type_point_odj: str | None = None
    ordre_point: int | None = None
    num_seance_jo: str | None = None
    quantieme: str | None = None
    speakers: list[SpeakerSnapshot] = Field(default_factory=list)
    intervenants_stats: list[SpeakerStat] = Field(default_factory=list)
    groupes_stats: list[GroupStat] = Field(default_factory=list)
    sommaire_source: list[DiscussionOutlineItem] = Field(default_factory=list)
    interventions: list[InterventionExcerpt] = Field(default_factory=list)
    procedure_events: list[DebateContextEvent] = Field(default_factory=list)
    interruptions: list[DebateContextEvent] = Field(default_factory=list)
    source_refs: DiscussionSourceRefs
    original_intervention_count: int = 0
    original_text_chars: int = 0
    input_text_chars: int = 0
    input_truncated: bool = False


class DebatTopic(BaseModel):
    sujet: str
    resume: str
    consensus: str | None = None
    tensions: str | None = None


class SpeakerPosition(BaseModel):
    speaker_id: str
    nom: str
    acteur: ActorRef | None = None
    groupe: str | None = None
    groupe_ref: GroupRef | None = None
    position: Position = "indetermine"
    arguments: list[str] = Field(default_factory=list)
    nuance: str | None = None


class LLMSpeakerPosition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    speaker_id: str
    nom: str
    groupe: str | None = None
    position: Position = "indetermine"
    arguments: list[str] = Field(default_factory=list)
    nuance: str | None = None


class GroupSynthesis(BaseModel):
    groupe: str
    groupe_ref: GroupRef | None = None
    position_dominante: Position = "indetermine"
    participation: ParticipationLevel = "indetermine"
    stats: GroupStat | None = None
    synthese: str
    nuances_internes: str | None = None
    intervenants_cles: list[str] = Field(default_factory=list)


class LLMGroupSynthesis(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    groupe: str
    position_dominante: Position = "indetermine"
    synthese: str
    nuances_internes: str | None = None
    intervenants_cles: list[str] = Field(default_factory=list)


class DiscussionOutlineSummaryItem(BaseModel):
    titre: str
    resume: str
    ordre: int | None = None


class DebateDynamics(BaseModel):
    ton_general: str
    niveau_conflit: ConflictLevel = "indetermine"
    caractere: list[str] = Field(default_factory=list)
    moments_marquants: list[str] = Field(default_factory=list)


class LLMDebateDynamics(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ton_general: str = Field(alias="ton")
    niveau_conflit: ConflictLevel = Field(default="indetermine", alias="conflit")
    caractere: list[str] = Field(default_factory=list, alias="types")
    moments_marquants: list[str] = Field(default_factory=list, alias="moments")


class DebatDiscussionSummary(BaseModel):
    resume_court: str
    sommaire_discussion: list[DiscussionOutlineSummaryItem] = Field(default_factory=list)
    sujets: list[DebatTopic] = Field(default_factory=list)
    positions_intervenants_principaux: list[SpeakerPosition] = Field(default_factory=list)
    synthese_groupes: list[GroupSynthesis] = Field(default_factory=list)
    dynamique_debat: DebateDynamics

    @field_validator("dynamique_debat", mode="before")
    @classmethod
    def normalize_dynamique_debat(cls, value):
        if isinstance(value, DebateDynamics):
            return value
        if isinstance(value, LLMDebateDynamics):
            return DebateDynamics(**value.model_dump())
        if isinstance(value, BaseModel):
            value = value.model_dump()
        if isinstance(value, dict) and any(
            key in value for key in ("ton", "conflit", "types", "moments")
        ):
            value = {
                "ton_general": value.get("ton_general") or value.get("ton"),
                "niveau_conflit": value.get("niveau_conflit")
                or value.get("conflit", "indetermine"),
                "caractere": value.get("caractere") or value.get("types", []),
                "moments_marquants": value.get("moments_marquants") or value.get("moments", []),
            }
        return value


class LLMDebatDiscussionSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resume_court: str = Field(alias="resume")
    sommaire_discussion: list[DiscussionOutlineSummaryItem] = Field(
        default_factory=list,
        alias="sommaire",
    )
    sujets: list[DebatTopic] = Field(default_factory=list)
    positions_intervenants_principaux: list[LLMSpeakerPosition] = Field(
        default_factory=list,
        alias="positions",
    )
    synthese_groupes: list[LLMGroupSynthesis] = Field(default_factory=list, alias="groupes")
    dynamique_debat: LLMDebateDynamics = Field(alias="dynamique")


class DebatDiscussionEnrichment(DebatDiscussionSummary):
    dossier_uid: str
    discussion_uid: str
    debat_uid: str | None = None
    reunion_uid: str | None = None
    point_odj_uid: str | None = None
    date_seance: datetime | None = None
    intervenants_stats: list[SpeakerStat] = Field(default_factory=list)
    groupes_stats: list[GroupStat] = Field(default_factory=list)
    source_refs: DiscussionSourceRefs
    input_truncated: bool = False
    model_name: str
    agent_version: str
    processed_at: datetime

    @property
    def document_id(self) -> str:
        return self.discussion_uid


class CumulativeDebatInputPack(BaseModel):
    dossier_uid: str
    dossier_titre: str | None = None
    discussion_summaries: list[DebatDiscussionEnrichment] = Field(default_factory=list)


class RecurringTopic(BaseModel):
    sujet: str
    synthese: str
    evolution: str | None = None


class CumulativeGroupSynthesis(BaseModel):
    groupe: str
    groupe_ref: GroupRef | None = None
    synthese: str
    position_dominante: Position = "indetermine"
    nuances: str | None = None


class LLMCumulativeGroupSynthesis(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    groupe: str
    synthese: str
    position_dominante: Position = "indetermine"
    nuances: str | None = None


class CumulativeDebatSynthesis(BaseModel):
    dossier_uid: str
    debate_summary_refs: list[str] = Field(default_factory=list)
    resume_global: str
    evolution_positions: str | None = None
    sujets_recurrents: list[RecurringTopic] = Field(default_factory=list)
    lignes_de_clivage: list[str] = Field(default_factory=list)
    groupes: list[CumulativeGroupSynthesis] = Field(default_factory=list)
    moments_marquants: list[str] = Field(default_factory=list)
    model_name: str = ""
    agent_version: str = ""
    processed_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class LLMCumulativeDebatSynthesis(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resume_global: str = Field(alias="resume")
    evolution_positions: str | None = Field(default=None, alias="evolution")
    sujets_recurrents: list[RecurringTopic] = Field(default_factory=list, alias="sujets")
    lignes_de_clivage: list[str] = Field(default_factory=list, alias="clivages")
    groupes: list[LLMCumulativeGroupSynthesis] = Field(default_factory=list)
    moments_marquants: list[str] = Field(default_factory=list, alias="moments")
