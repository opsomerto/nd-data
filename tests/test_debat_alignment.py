from datetime import datetime, timezone

from nd_data.debat_summary.alignment import build_alignment_document
from nd_data.debat_summary.sources import build_debat_discussion_packs_from_alignments
from nd_data.tricoteuse_models import Agenda, Debat, Dossier, Paragraphe, PointOdj


def paragraph(
    uid: str,
    section: str,
    text: str,
    dossier_uid: str | None = None,
    code_grammaire: str = "TITRE_TEXTE_DISCUSSION",
):
    return Paragraphe(
        uid=uid,
        valeurPtsOdj=section,
        texte=text,
        dossierRefUid=dossier_uid,
        codeGrammaire=code_grammaire,
        ordreAbsoluSeance=int(uid[1:]),
        debatRefUid="DEB1",
        reunionRefUid="R1",
    )


class FakeClient:
    dossiers = {
        "D1": Dossier(uid="D1", titre="Contre les déserts médicaux, d'initiative transpartisane"),
        "D2": Dossier(
            uid="D2",
            titre="Renforcer le contrôle du Parlement en période d'expédition des affaires courantes",
        ),
    }

    def __init__(self, debat: Debat, reunion: Agenda):
        self.debat = debat
        self.reunion = reunion

    def get_dossier(self, uid: str, include=None):
        return self.dossiers.get(uid)

    def get_reunion(self, uid: str, include=None):
        return self.reunion

    def get_debat(self, uid: str, include=None):
        return self.debat

    def get_acteur(self, uid: str, include=None):
        return None


def test_alignment_matches_real_section_title_over_misleading_metadata():
    reunion = Agenda(
        uid="R1",
        compteRenduRefUid="DEB1",
        dateSeance=datetime(2025, 3, 1, tzinfo=timezone.utc),
        pointsOdj=[
            PointOdj(uid="P_D2", dossierLegislatifUid="D2", ordrePoint=3),
            PointOdj(uid="P_D1", dossierLegislatifUid="D1", ordrePoint=4),
        ],
    )
    debat = Debat(
        uid="DEB1",
        paragraphes=[
            paragraph(
                "P1",
                "4",
                "Proposition de loi visant à renforcer le contrôle du Parlement",
                dossier_uid="D1",
            ),
            paragraph(
                "P2",
                "5",
                "Contre les déserts médicaux, d'initiative transpartisane",
                dossier_uid=None,
            ),
        ],
    )
    client = FakeClient(debat, reunion)

    doc = build_alignment_document(client, reunion, debat, seed_dossiers=[client.dossiers["D1"]])

    by_section = {section.real_ordre_point: section for section in doc.sections}
    assert by_section["4"].matched_dossier_uid == "D2"
    assert "paragraph_metadata_points_to_other_dossier" in by_section["4"].warnings
    assert by_section["5"].matched_dossier_uid == "D1"
    assert by_section["5"].planned_ordre_point == 4
    assert "planned_ordre_point_differs_from_real_section" in by_section["5"].warnings


def test_summary_pack_from_alignment_uses_real_section_not_metadata():
    reunion = Agenda(uid="R1", compteRenduRefUid="DEB1")
    debat = Debat(
        uid="DEB1",
        paragraphes=[
            paragraph("P1", "4", "Wrong section title", dossier_uid="D1"),
            Paragraphe(
                uid="P2",
                valeurPtsOdj="5",
                texte="Je défends la proposition contre les déserts médicaux.",
                dossierRefUid=None,
                codeGrammaire="PAROLE_GENERIQUE",
                acteurRefUid="A1",
                orateur="Alice Martin",
                ordreAbsoluSeance=2,
                debatRefUid="DEB1",
                reunionRefUid="R1",
            ),
        ],
    )
    client = FakeClient(debat, reunion)
    alignment = build_alignment_document(
        client, reunion, debat, seed_dossiers=[client.dossiers["D1"]]
    )

    packs = build_debat_discussion_packs_from_alignments(
        client.dossiers["D1"],
        client,
        [alignment],
    )

    assert len(packs) == 1
    assert (
        packs[0].interventions[0].text == "Je défends la proposition contre les déserts médicaux."
    )


def test_alignment_ignores_opening_and_closing_procedural_sections():
    reunion = Agenda(
        uid="R1",
        compteRenduRefUid="DEB1",
        pointsOdj=[PointOdj(uid="P_D1", dossierLegislatifUid="D1", ordrePoint=4)],
    )
    debat = Debat(
        uid="DEB1",
        paragraphes=[
            paragraph(
                "P1",
                "1",
                "Ouverture de la séance",
                dossier_uid="D1",
                code_grammaire="OUV_SEAN_1_1",
            ),
            paragraph(
                "P2",
                "5",
                "Contre les déserts médicaux, d'initiative transpartisane",
            ),
            paragraph(
                "P3",
                "6",
                "Ordre du jour de la prochaine séance",
                dossier_uid="D1",
                code_grammaire="FIN_SEAN_2_1",
            ),
        ],
    )
    client = FakeClient(debat, reunion)

    doc = build_alignment_document(client, reunion, debat, seed_dossiers=[client.dossiers["D1"]])

    by_section = {section.real_ordre_point: section for section in doc.sections}
    assert by_section["1"].matched_dossier_uid is None
    assert by_section["1"].confidence == "unresolved"
    assert "procedural_section_ignored" in by_section["1"].warnings
    assert by_section["5"].matched_dossier_uid == "D1"
    assert by_section["6"].matched_dossier_uid is None
    assert by_section["6"].confidence == "unresolved"
    assert "procedural_section_ignored" in by_section["6"].warnings


def test_specific_unrelated_title_does_not_match_from_metadata_only():
    reunion = Agenda(
        uid="R1",
        compteRenduRefUid="DEB1",
        pointsOdj=[PointOdj(uid="P_D1", dossierLegislatifUid="D1", ordrePoint=4)],
    )
    debat = Debat(
        uid="DEB1",
        paragraphes=[
            paragraph(
                "P1",
                "4",
                "Proposition de loi visant à renforcer le contrôle du Parlement",
                dossier_uid="D1",
            ),
        ],
    )
    client = FakeClient(debat, reunion)

    doc = build_alignment_document(client, reunion, debat, seed_dossiers=[client.dossiers["D1"]])

    assert doc.sections[0].matched_dossier_uid is None
    assert doc.sections[0].score <= 0.2
