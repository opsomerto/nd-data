"""Models for actor-dossier evidence and actor-topic profiles."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


TopicNamespace = Literal["senat_theme", "open_theme", "keyword"]


class EvidenceKind(str, Enum):
    initiateur_dossier = "initiateur_dossier"
    acteur_principal = "acteur_principal"
    rapporteur = "rapporteur"
    auteur_principal_document = "auteur_principal_document"
    auteur_document = "auteur_document"
    cosignataire_document = "cosignataire_document"
    auteur_motion = "auteur_motion"
    initiateur_acte = "initiateur_acte"
    rapporteur_acte = "rapporteur_acte"
    amendement_depose = "amendement_depose"
    amendement_cosigne = "amendement_cosigne"
    intervention_debat = "intervention_debat"
    presence_commission = "presence_commission"


class TopicRef(BaseModel):
    namespace: TopicNamespace
    key: str
    label: str | None = None


class EvidenceItem(BaseModel):
    kind: EvidenceKind
    source_uid: str | None = None
    source_type: str | None = None
    date: datetime | None = None
    score_units: int = 1
    details: dict[str, Any] = Field(default_factory=dict)
    text: str | None = None


class ActorSnapshot(BaseModel):
    uid: str
    nom: str | None = None
    prenom: str | None = None
    groupe_uid: str | None = None


class ActorDossierEvidence(BaseModel):
    acteur_uid: str
    dossier_uid: str
    actor: ActorSnapshot | None = None
    topics: list[TopicRef] = Field(default_factory=list)
    action_counts: dict[str, int] = Field(default_factory=dict)
    raw_score: float = 0.0
    stale: bool = False
    computed_at: datetime
    run_id: str
    extractor_version: str
    tricoteuse_participant_snapshot: dict[str, Any] | None = None

    @property
    def document_id(self) -> str:
        return f"{self.acteur_uid}:{self.dossier_uid}"


class ThemeScore(BaseModel):
    namespace: TopicNamespace
    key: str
    label: str | None = None
    score: float = 0.0
    normalized_score: float = 0.0
    dossier_count: int = 0
    action_counts: dict[str, int] = Field(default_factory=dict)


class ActorThemeProfile(BaseModel):
    acteur_uid: str
    actor: ActorSnapshot | None = None
    main_senat_themes: list[ThemeScore] = Field(default_factory=list)
    main_open_themes: list[ThemeScore] = Field(default_factory=list)
    main_keywords: list[ThemeScore] = Field(default_factory=list)
    total_score: float = 0.0
    dossier_count: int = 0
    computed_at: datetime
    run_id: str
    scoring_version: str
