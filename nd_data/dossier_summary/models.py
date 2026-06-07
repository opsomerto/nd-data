"""Pydantic models for dossier summary enrichment."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from nd_data.dossier_summary.themes import THEME_LABELS, normalize_senat_theme


class SummaryModule(str, Enum):
    qualification = "qualification"
    structured_summary = "structured_summary"
    navette_situation = "navette_situation"


class SourceKind(str, Enum):
    initial = "initial"
    annex = "annex"
    report = "report"
    adopted_text = "adopted_text"
    other = "other"


class SourceDocument(BaseModel):
    uid: str
    kind: SourceKind
    title: str | None = None
    type_code: str | None = None
    type_label: str | None = None
    sous_type_code: str | None = None
    sous_type_label: str | None = None
    acte_code: str | None = None
    pdf_url: str | None = None
    text: str | None = None
    text_origin: Literal["model", "pdf", "none"] = "none"
    original_text_chars: int | None = None
    text_chars: int | None = None
    text_truncated: bool = False


class DossierInputPack(BaseModel):
    uid: str
    titre: str | None = None
    chambre: str | None = None
    legislature: int | None = None
    type_initiative: str | None = None
    type_procedure: str | None = None
    code_procedure: str | None = None
    statut: str | None = None
    etat: str | None = None
    date_depot: datetime | None = None
    date_dernier_acte: datetime | None = None
    code_dernier_acte: str | None = None
    uid_dernier_acte: str | None = None
    procedure_acceleree: bool | None = None
    retrait_initiative: bool | None = None
    source_documents: list[SourceDocument] = Field(default_factory=list)
    navette_facts: "NavetteFacts | None" = None


class Qualification(BaseModel):
    themes_senat: list[str] = Field(
        default_factory=list,
        description="Labels normalises parmi la liste des themes Senat.",
        max_length=5,
    )
    themes_ouverts: list[str] = Field(default_factory=list, max_length=10)
    keywords: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("themes_senat", mode="before")
    @classmethod
    def normalize_themes_senat(cls, value: list[str] | None) -> list[str]:
        if not value:
            return []
        normalized = []
        for item in value:
            label = normalize_senat_theme(str(item))
            if label in THEME_LABELS and label not in normalized:
                normalized.append(label)
        return normalized[:5]


class Enjeu(BaseModel):
    sujet: str
    importance: Literal["faible", "moderee", "elevee", "critique"]
    description: str
    arbitrage: str | None = None


class ActeurConcerne(BaseModel):
    acteur: str
    impact: str


class StructuredSummary(BaseModel):
    tldr: str = Field(description="Une phrase expliquant concrètement ce que ferait le texte.")
    pourquoi: str
    enjeux: list[Enjeu] = Field(min_length=1, max_length=5)
    ce_qui_change: list[str] = Field(min_length=1, max_length=6)
    acteurs_concernes: list[ActeurConcerne] = Field(default_factory=list, max_length=8)
    objectif: str


class NavetteTimelineItem(BaseModel):
    date: datetime | None = None
    chambre: str | None = None
    label: str
    acte_uid: str | None = None
    acte_code: str | None = None


class NavetteActivityMetrics(BaseModel):
    nb_actes: int = 0
    nb_commissions: int = 0
    commissions: list[str] = Field(default_factory=list)
    nb_debats_seance: int = 0
    temps_debat_seance_minutes: int | None = None
    nb_amendements: int | None = None
    nb_documents: int = 0
    nb_lectures: int = 0
    has_cmp: bool = False
    has_promulgation: bool = False


class NavetteFacts(BaseModel):
    current_status: str | None = None
    current_state: str | None = None
    current_chamber: str | None = None
    last_acte_uid: str | None = None
    last_acte_code: str | None = None
    last_acte_label: str | None = None
    metrics: NavetteActivityMetrics = Field(default_factory=NavetteActivityMetrics)
    timeline: list[NavetteTimelineItem] = Field(default_factory=list)
    evidence_acte_uids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class NavetteSituation(BaseModel):
    resume: str
    current_stage: str | None = None
    current_chamber: str | None = None
    procedure_state: str | None = None
    intensity: Literal["faible", "moderee", "forte", "inconnue"] = "inconnue"
    timeline: list[str] = Field(default_factory=list, max_length=8)
    evidence_acte_uids: list[str] = Field(default_factory=list)


class DossierSummaryOutput(BaseModel):
    qualification: Qualification | None = None
    structured_summary: StructuredSummary | None = None
    navette_situation: NavetteSituation | None = None


class DossierSummaryEnrichment(DossierSummaryOutput):
    model_name: str
    agent_version: str
    modules_processed: list[SummaryModule]
    source_options: list[str]
    source_document_refs: list[str]
    processed_at: datetime
