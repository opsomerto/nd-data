from datetime import datetime, timezone

from nd_data.dossier_summary.navette import build_navette_facts
from nd_data.dossier_summary.sources import build_dossier_input_pack
from nd_data.tricoteuse_models import Agenda
from nd_data.tricoteuse_models import ActeLegislatif, ChambreEnum, Dossier


def test_build_navette_facts_counts_commission_and_debates():
    dossier = Dossier(
        uid="D1",
        titre="Projet de loi test",
        chambre=ChambreEnum.AN,
        statut="En cours",
        etat="En cours",
        uidDernierActe="A3",
        codeDernierActe="AN1-DEBATS-SEANCE",
        actesLegislatifs=[
            ActeLegislatif(
                uid="A1",
                chambre=ChambreEnum.AN,
                codeActe="AN1-DEPOT",
                dateActe=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            ActeLegislatif(
                uid="A2",
                chambre=ChambreEnum.AN,
                codeActe="AN1-COM-FOND",
                organeRefUid="PO59051",
                dateActe=datetime(2026, 1, 3, tzinfo=timezone.utc),
            ),
            ActeLegislatif(
                uid="A3",
                chambre=ChambreEnum.AN,
                codeActe="AN1-DEBATS-SEANCE",
                dateActe=datetime(2026, 1, 5, tzinfo=timezone.utc),
            ),
        ],
    )

    facts = build_navette_facts(dossier)

    assert facts.last_acte_uid == "A3"
    assert facts.metrics.nb_actes == 3
    assert facts.metrics.nb_commissions == 1
    assert facts.metrics.nb_debats_seance == 1
    assert facts.metrics.nb_lectures == 1
    assert facts.evidence_acte_uids == ["A1", "A2", "A3"]


class FakeCountResponse:
    def count(self, field_name: str) -> int:
        assert field_name == "amendements"
        return 42


class FakeClient:
    def get_dossier(self, uid: str, computed_fields: list[str]):
        assert uid == "D1"
        assert computed_fields == ["_count.amendements"]
        return FakeCountResponse()

    def get_reunion(self, uid: str):
        assert uid == "R1"
        return Agenda(
            uid="R1",
            timestampDebut=datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc),
            timestampFin=datetime(2026, 1, 5, 16, 30, tzinfo=timezone.utc),
        )


def test_build_input_pack_can_fetch_amendments_and_debate_duration():
    dossier = Dossier(
        uid="D1",
        titre="Projet de loi test",
        chambre=ChambreEnum.AN,
        actesLegislatifs=[
            ActeLegislatif(
                uid="A1",
                chambre=ChambreEnum.AN,
                codeActe="AN1-DEBATS-SEANCE",
                reunionRefUid="R1",
                dateActe=datetime(2026, 1, 5, tzinfo=timezone.utc),
            )
        ],
    )

    pack = build_dossier_input_pack(
        dossier,
        client=FakeClient(),
        fetch_amendment_count=True,
        fetch_debate_durations=True,
    )

    assert pack.navette_facts.metrics.nb_amendements == 42
    assert pack.navette_facts.metrics.temps_debat_seance_minutes == 150
    assert pack.navette_facts.notes == []
