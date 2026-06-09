"""Extract actor-dossier evidence from Tricoteuses resources."""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from nd_data.acteur_topics.models import (
    ActorDossierEvidence,
    EvidenceItem,
    EvidenceKind,
)
from nd_data.acteur_topics.scoring import DEFAULT_WEIGHTS, ScoringWeights, score_actor_dossier
from nd_data.dossier_summary.navette import is_commission_acte
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import (
    ActeLegislatif,
    Agenda,
    Amendement,
    Document,
    Dossier,
    Paragraphe,
    ParticipantDossier,
)

EXTRACTOR_VERSION = "acteur_topics_extractor_v1"
MEANINGFUL_INTERVENTION_CODES = {"PAROLE_GENERIQUE"}
MIN_INTERVENTION_WORDS = 8


def enum_value(value):
    return getattr(value, "value", value)


def source_uid(kind: str, value: object | None) -> str:
    if value is None:
        return kind
    return f"{kind}:{value}"


def add_item(
    evidence_by_actor: dict[str, list[EvidenceItem]],
    acteur_uid: str | None,
    item: EvidenceItem,
) -> None:
    if acteur_uid:
        evidence_by_actor[acteur_uid].append(item)


def extract_initiators(dossier: Dossier) -> dict[str, list[EvidenceItem]]:
    items: dict[str, list[EvidenceItem]] = defaultdict(list)
    for initiateur in dossier.initiateurs or []:
        add_item(
            items,
            initiateur.acteurRefUid,
            EvidenceItem(
                kind=EvidenceKind.initiateur_dossier,
                source_uid=source_uid("initiateur", initiateur.id),
                source_type="InitiateurDossier",
                date=dossier.dateDepot,
                details={"mandat_ref_uid": initiateur.mandatRefUid},
            ),
        )
    if dossier.acteurPrincipalRefUid:
        add_item(
            items,
            dossier.acteurPrincipalRefUid,
            EvidenceItem(
                kind=EvidenceKind.acteur_principal,
                source_uid=source_uid("acteurPrincipal", dossier.uid),
                source_type="Dossier",
                date=dossier.dateDepot,
            ),
        )
    return items


def extract_rapporteurs(dossier: Dossier) -> dict[str, list[EvidenceItem]]:
    items: dict[str, list[EvidenceItem]] = defaultdict(list)
    for rapporteur in dossier.rapporteurs or []:
        add_item(
            items,
            rapporteur.acteurRefUid,
            EvidenceItem(
                kind=EvidenceKind.rapporteur,
                source_uid=source_uid("rapporteur", rapporteur.id),
                source_type="Rapporteur",
                details={
                    "type_rapporteur": rapporteur.typeRapporteur,
                    "acte_legislatif_ref_uid": rapporteur.acteLegislatifRefUid,
                },
            ),
        )
    return items


def extract_documents(documents: list[Document]) -> dict[str, list[EvidenceItem]]:
    items: dict[str, list[EvidenceItem]] = defaultdict(list)
    for document in documents:
        add_item(
            items,
            document.auteurPrincipalUid,
            EvidenceItem(
                kind=EvidenceKind.auteur_principal_document,
                source_uid=document.uid,
                source_type="Document",
                date=document.dateDepot,
                details={"titre": document.titrePrincipal},
            ),
        )
        for auteur in document.auteurs or []:
            add_item(
                items,
                auteur.acteurRefUid,
                EvidenceItem(
                    kind=EvidenceKind.auteur_document,
                    source_uid=document.uid,
                    source_type="AuteurDocument",
                    date=document.dateDepot,
                    details={"qualite": auteur.qualite, "document_ref_uid": auteur.documentRefUid},
                ),
            )
        for cosignataire in document.coSignataires or []:
            if cosignataire.dateRetraitCosignature is not None:
                continue
            add_item(
                items,
                cosignataire.acteurRefUid,
                EvidenceItem(
                    kind=EvidenceKind.cosignataire_document,
                    source_uid=document.uid,
                    source_type="CoSignataireDocument",
                    date=cosignataire.dateCosignature or document.dateDepot,
                    details={"document_ref_uid": cosignataire.documentRefUid},
                ),
            )
    return items


def extract_acte_signals(actes: list[ActeLegislatif]) -> dict[str, list[EvidenceItem]]:
    items: dict[str, list[EvidenceItem]] = defaultdict(list)
    for acte in actes:
        add_item(
            items,
            acte.auteurMotionRefUid,
            EvidenceItem(
                kind=EvidenceKind.auteur_motion,
                source_uid=acte.uid,
                source_type="ActeLegislatif",
                date=acte.dateActe,
                details={"code_acte": acte.codeActe},
            ),
        )
        for auteur in acte.auteursRefs or []:
            add_item(
                items,
                auteur.auteurMotionRefUid,
                EvidenceItem(
                    kind=EvidenceKind.auteur_motion,
                    source_uid=acte.uid,
                    source_type="AuteurMotion",
                    date=acte.dateActe,
                    details={"auteur_motion_id": auteur.id, "code_acte": acte.codeActe},
                ),
            )
        for initiateur in acte.initiateurActeLegislatif or []:
            add_item(
                items,
                initiateur.acteurRefUid,
                EvidenceItem(
                    kind=EvidenceKind.initiateur_acte,
                    source_uid=acte.uid,
                    source_type="InitiateurActeLegislatif",
                    date=acte.dateActe,
                    details={"mandat_ref_uid": initiateur.mandatRefUid, "code_acte": acte.codeActe},
                ),
            )
        for rapporteur in acte.rapporteurs or []:
            add_item(
                items,
                rapporteur.acteurRefUid,
                EvidenceItem(
                    kind=EvidenceKind.rapporteur_acte,
                    source_uid=acte.uid,
                    source_type="Rapporteur",
                    date=acte.dateActe,
                    details={
                        "type_rapporteur": rapporteur.typeRapporteur,
                        "code_acte": acte.codeActe,
                    },
                ),
            )
    return items


def extract_amendments(amendements: list[Amendement]) -> dict[str, list[EvidenceItem]]:
    items: dict[str, list[EvidenceItem]] = defaultdict(list)
    for amendement in amendements:
        details = {
            "numero": amendement.numeroLong,
            "sort": amendement.sortAmendement,
            "etat_code": amendement.etatCode,
            "type_auteur": amendement.typeAuteur,
            "document_uri": amendement.documentURI,
        }
        add_item(
            items,
            amendement.acteurRefUid,
            EvidenceItem(
                kind=EvidenceKind.amendement_depose,
                source_uid=amendement.uid,
                source_type="Amendement",
                date=amendement.dateDepot,
                details={key: value for key, value in details.items() if value is not None},
                text=amendement.exposeSommaire,
            ),
        )
        for cosignataire in amendement.coSignataires or []:
            if cosignataire.acteurRefUid == amendement.acteurRefUid:
                continue
            add_item(
                items,
                cosignataire.acteurRefUid,
                EvidenceItem(
                    kind=EvidenceKind.amendement_cosigne,
                    source_uid=amendement.uid,
                    source_type="Amendement",
                    date=amendement.dateDepot,
                    details={"cosignature_uid": cosignataire.uid},
                ),
            )
    return items


def is_meaningful_intervention(intervention: Paragraphe) -> bool:
    if not intervention.acteurRefUid or not intervention.texte:
        return False
    if intervention.estPresident:
        return False
    if intervention.codeGrammaire not in MEANINGFUL_INTERVENTION_CODES:
        return False
    return len(intervention.texte.split()) >= MIN_INTERVENTION_WORDS


def extract_interventions(interventions: list[Paragraphe]) -> dict[str, list[EvidenceItem]]:
    items: dict[str, list[EvidenceItem]] = defaultdict(list)
    for intervention in interventions:
        if not is_meaningful_intervention(intervention):
            continue
        add_item(
            items,
            intervention.acteurRefUid,
            EvidenceItem(
                kind=EvidenceKind.intervention_debat,
                source_uid=intervention.uid,
                source_type="Paragraphe",
                date=intervention.dateSeance,
                details={
                    "debat_ref_uid": intervention.debatRefUid,
                    "reunion_ref_uid": intervention.reunionRefUid,
                    "valeur_pts_odj": intervention.valeurPtsOdj,
                    "code_grammaire": intervention.codeGrammaire,
                    "ordre_absolu_seance": intervention.ordreAbsoluSeance,
                },
                text=intervention.texte,
            ),
        )
    return items


def commission_reunion_uids(actes: list[ActeLegislatif]) -> list[str]:
    seen = set()
    uids = []
    for acte in actes:
        if not is_commission_acte(acte) or not acte.reunionRefUid or acte.reunionRefUid in seen:
            continue
        seen.add(acte.reunionRefUid)
        uids.append(acte.reunionRefUid)
    return uids


def extract_commission_presence(reunions: list[Agenda]) -> dict[str, list[EvidenceItem]]:
    items: dict[str, list[EvidenceItem]] = defaultdict(list)
    for reunion in reunions:
        for participant in reunion.participantsInternes or []:
            if enum_value(participant.presence) != "présent":
                continue
            add_item(
                items,
                participant.acteurRefUid,
                EvidenceItem(
                    kind=EvidenceKind.presence_commission,
                    source_uid=reunion.uid,
                    source_type="Agenda",
                    date=reunion.timestampDebut,
                    details={
                        "agenda_ref_uid": participant.agendaRefUid,
                        "orateur": participant.orateur,
                        "organe_reunion_ref_uid": reunion.organeReunionRefUid,
                    },
                ),
            )
    return items


def merge_evidence(
    evidence_groups: list[dict[str, list[EvidenceItem]]],
) -> dict[str, list[EvidenceItem]]:
    merged: dict[str, list[EvidenceItem]] = defaultdict(list)
    for group in evidence_groups:
        for acteur_uid, items in group.items():
            merged[acteur_uid].extend(items)
    return merged


def action_counts(items: list[EvidenceItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.kind.value] = counts.get(item.kind.value, 0) + item.score_units
    return counts


def participant_snapshot(participant: ParticipantDossier | None) -> dict | None:
    if participant is None:
        return None
    return participant.model_dump(exclude={"dossierRef", "acteurRef"}, exclude_none=True)


def build_evidence_docs(
    dossier: Dossier,
    evidence_by_actor: dict[str, list[EvidenceItem]],
    participants: dict[str, ParticipantDossier] | None = None,
    run_id: str | None = None,
    computed_at: datetime | None = None,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> list[ActorDossierEvidence]:
    if computed_at is None:
        computed_at = datetime.now(UTC)
    if run_id is None:
        run_id = uuid4().hex
    docs = []
    for acteur_uid, items in evidence_by_actor.items():
        doc = ActorDossierEvidence(
            acteur_uid=acteur_uid,
            dossier_uid=dossier.uid or "",
            action_counts=action_counts(items),
            raw_score=0,
            computed_at=computed_at,
            run_id=run_id,
            extractor_version=EXTRACTOR_VERSION,
            tricoteuse_participant_snapshot=participant_snapshot(
                participants.get(acteur_uid) if participants else None
            ),
        )
        doc.raw_score = score_actor_dossier(doc, weights)
        docs.append(doc)
    return sorted(docs, key=lambda doc: (-doc.raw_score, doc.acteur_uid))


def fetch_paginated(fetch_page, per_page: int, min_per_page: int = 10) -> list:
    current_per_page = max(1, per_page)
    page = 1
    items = []
    while True:
        try:
            batch = fetch_page(page, current_per_page)
        except Exception:
            if current_per_page <= min_per_page:
                raise
            current_per_page = max(min_per_page, current_per_page // 2)
            page = 1
            items = []
            continue
        items.extend(batch)
        if len(batch) < current_per_page:
            return items
        page += 1


def collect_dossier_evidence(
    client: TricoteuseAPIClient,
    dossier_uid: str,
    run_id: str | None = None,
    computed_at: datetime | None = None,
    per_page: int = 100,
    include_tricoteuse_participants: bool = True,
) -> list[ActorDossierEvidence]:
    dossier = client.get_dossier(
        dossier_uid,
        include=["initiateurs", "rapporteurs", "actesLegislatifs"],
    )
    if dossier is None:
        return []

    actes = dossier.actesLegislatifs or []
    documents = fetch_paginated(
        lambda page, size: client.get_documents(
            page=page,
            per_page=size,
            include=["auteurs", "coSignataires"],
            dossierRefUid=dossier_uid,
        ),
        per_page,
    )
    amendements = fetch_paginated(
        lambda page, size: client.get_amendements(
            page=page,
            per_page=size,
            include=["coSignataires"],
            dossierRefUid=dossier_uid,
        ),
        per_page,
    )
    interventions = fetch_paginated(
        lambda page, size: client.get_interventions(
            page=page,
            per_page=size,
            dossierRefUid=dossier_uid,
        ),
        per_page,
    )
    reunions = []
    for reunion_uid in commission_reunion_uids(actes):
        reunion = client.get_reunion(reunion_uid, include=["participantsInternes"])
        if reunion:
            reunions.append(reunion)

    participants = {}
    if include_tricoteuse_participants:
        try:
            participant_dossier = client.get_dossier(dossier_uid, include=["participantsDossiers"])
            participants = {
                item.acteurRefUid: item
                for item in (participant_dossier.participantsDossiers or [])
                if item.acteurRefUid
            }
        except Exception:
            participants = {}

    return build_evidence_docs(
        dossier,
        merge_evidence(
            [
                extract_initiators(dossier),
                extract_rapporteurs(dossier),
                extract_documents(documents),
                extract_acte_signals(actes),
                extract_amendments(amendements),
                extract_interventions(interventions),
                extract_commission_presence(reunions),
            ]
        ),
        participants=participants,
        run_id=run_id,
        computed_at=computed_at,
    )
