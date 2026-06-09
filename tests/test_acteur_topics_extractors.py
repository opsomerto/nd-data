from datetime import UTC, datetime

from nd_data.acteur_topics.extractors import (
    build_evidence_docs,
    extract_acte_signals,
    extract_amendments,
    extract_commission_presence,
    extract_documents,
    extract_initiators,
    extract_interventions,
    extract_rapporteurs,
    fetch_paginated,
    is_meaningful_intervention,
)
from nd_data.acteur_topics.models import EvidenceKind
from nd_data.tricoteuse_models import (
    Agenda,
    ActeLegislatif,
    Amendement,
    AuteurDocument,
    AuteurMotion,
    CoSignataireAmendement,
    CoSignataireDocument,
    Document,
    Dossier,
    InitiateurDossier,
    InitiateurActeLegislatif,
    Paragraphe,
    ParticipantReunion,
    Presence,
    Rapporteur,
)


def test_extract_initiators_and_rapporteurs():
    dossier = Dossier(
        uid="D1",
        titre="Dossier",
        dateDepot=datetime(2026, 1, 1, tzinfo=UTC),
        acteurPrincipalRefUid="PA1",
        initiateurs=[InitiateurDossier(id=1, acteurRefUid="PA2", mandatRefUid="PM2")],
        rapporteurs=[Rapporteur(id=3, acteurRefUid="PA3", typeRapporteur="rapporteur")],
    )

    initiators = extract_initiators(dossier)
    rapporteurs = extract_rapporteurs(dossier)

    assert [item.kind for item in initiators["PA1"]] == [EvidenceKind.acteur_principal]
    assert [item.kind for item in initiators["PA2"]] == [EvidenceKind.initiateur_dossier]
    assert [item.kind for item in rapporteurs["PA3"]] == [EvidenceKind.rapporteur]


def test_extract_amendment_author_and_cosignataire():
    amendement = Amendement(
        uid="AMD1",
        acteurRefUid="PA1",
        dateDepot=datetime(2026, 1, 2, tzinfo=UTC),
        numeroLong="Amdt 1",
        coSignataires=[
            CoSignataireAmendement(uid="C1", acteurRefUid="PA1"),
            CoSignataireAmendement(uid="C2", acteurRefUid="PA2"),
        ],
    )

    items = extract_amendments([amendement])

    assert [item.kind for item in items["PA1"]] == [EvidenceKind.amendement_depose]
    assert [item.kind for item in items["PA2"]] == [EvidenceKind.amendement_cosigne]


def test_extract_document_author_and_cosignataire():
    document = Document(
        uid="DOC1",
        auteurPrincipalUid="PA1",
        dateDepot=datetime(2026, 1, 2, tzinfo=UTC),
        classificationCode="PIONAN",
        titrePrincipal="Proposition de loi",
        auteurs=[AuteurDocument(uid="A1", acteurRefUid="PA2", qualite="Député")],
        coSignataires=[
            CoSignataireDocument(uid="C1", acteurRefUid="PA3"),
            CoSignataireDocument(
                uid="C2",
                acteurRefUid="PA4",
                dateRetraitCosignature=datetime(2026, 1, 3, tzinfo=UTC),
            ),
        ],
    )

    items = extract_documents([document])

    assert [item.kind for item in items["PA1"]] == [EvidenceKind.auteur_principal_document]
    assert [item.kind for item in items["PA2"]] == [EvidenceKind.auteur_document]
    assert [item.kind for item in items["PA3"]] == [EvidenceKind.cosignataire_document]
    assert "PA4" not in items


def test_extract_acte_motion_initiator_and_rapporteur():
    acte = ActeLegislatif(
        uid="ACTE1",
        codeActe="AN1-COM",
        auteurMotionRefUid="PA1",
        dateActe=datetime(2026, 1, 4, tzinfo=UTC),
        auteursRefs=[AuteurMotion(id=1, auteurMotionRefUid="PA2")],
        initiateurActeLegislatif=[
            InitiateurActeLegislatif(id=2, acteurRefUid="PA3", mandatRefUid="PM3")
        ],
        rapporteurs=[Rapporteur(id=3, acteurRefUid="PA4", typeRapporteur="rapporteur")],
    )

    items = extract_acte_signals([acte])

    assert [item.kind for item in items["PA1"]] == [EvidenceKind.auteur_motion]
    assert [item.kind for item in items["PA2"]] == [EvidenceKind.auteur_motion]
    assert [item.kind for item in items["PA3"]] == [EvidenceKind.initiateur_acte]
    assert [item.kind for item in items["PA4"]] == [EvidenceKind.rapporteur_acte]


def test_intervention_filter_keeps_only_meaningful_speech():
    valid = Paragraphe(
        uid="P1",
        acteurRefUid="PA1",
        codeGrammaire="PAROLE_GENERIQUE",
        texte="Un texte assez long pour représenter une vraie intervention politique.",
    )
    title = Paragraphe(
        uid="P2",
        acteurRefUid="PA1",
        codeGrammaire="TITRE_TEXTE_DISCUSSION",
        texte="Titre du dossier",
    )
    short = Paragraphe(
        uid="P3",
        acteurRefUid="PA1",
        codeGrammaire="PAROLE_GENERIQUE",
        texte="Très court.",
    )
    president = Paragraphe(
        uid="P4",
        acteurRefUid="PA1",
        codeGrammaire="PAROLE_GENERIQUE",
        texte="Un texte assez long pour être filtré car président de séance.",
        estPresident=True,
    )

    assert is_meaningful_intervention(valid)
    assert not is_meaningful_intervention(title)
    assert not is_meaningful_intervention(short)
    assert not is_meaningful_intervention(president)

    items = extract_interventions([valid, title, short, president])
    assert [item.source_uid for item in items["PA1"]] == ["P1"]


def test_extract_commission_presence_keeps_present_participants_only():
    reunion = Agenda(
        uid="R1",
        timestampDebut=datetime(2026, 1, 3, tzinfo=UTC),
        participantsInternes=[
            ParticipantReunion(acteurRefUid="PA1", presence=Presence.présent),
            ParticipantReunion(acteurRefUid="PA2", presence=Presence.absent),
            ParticipantReunion(acteurRefUid="PA3", presence=Presence.excusé),
        ],
    )

    items = extract_commission_presence([reunion])

    assert list(items) == ["PA1"]
    assert items["PA1"][0].kind == EvidenceKind.presence_commission


def test_build_evidence_docs_computes_counts_and_score():
    dossier = Dossier(uid="D1", titre="Dossier")
    evidence_by_actor = {
        "PA1": extract_amendments(
            [Amendement(uid=f"AMD{i}", acteurRefUid="PA1") for i in range(20)]
        )["PA1"]
    }

    docs = build_evidence_docs(dossier, evidence_by_actor, run_id="run")

    assert docs[0].action_counts == {"amendement_depose": 20}
    assert docs[0].raw_score == 30


def test_fetch_paginated_restarts_with_smaller_page_after_failure():
    calls = []

    def fetch_page(page: int, per_page: int):
        calls.append((page, per_page))
        if per_page > 25:
            raise RuntimeError("incomplete chunked read")
        if page == 1:
            return list(range(per_page))
        return [1]

    items = fetch_paginated(fetch_page, per_page=100, min_per_page=25)

    assert calls == [(1, 100), (1, 50), (1, 25), (2, 25)]
    assert len(items) == 26
