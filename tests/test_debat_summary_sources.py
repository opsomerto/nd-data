from datetime import datetime, timezone

from nd_data.debat_summary.sources import (
    build_debat_discussion_packs,
    build_speaker_context,
    filter_debat_interventions,
    get_actor_cached,
    point_is_valid_for_dossier,
    split_intervention_lanes,
)
from nd_data.tricoteuse_models import (
    ActeLegislatif,
    Acteur,
    Agenda,
    ChambreEnum,
    Debat,
    Dossier,
    Mandat,
    Organe,
    Paragraphe,
    PointOdj,
)


def intervention(
    uid: str,
    text: str,
    *,
    dossier_uid: str | None = "D1",
    point_uid: str | None = "P1",
    value: str | None = "1",
    actor_uid: str | None = "A1",
    orateur: str | None = "Alice Martin",
    order: int = 1,
    president: bool = False,
    sommaire: str | None = None,
    role: str | None = None,
    code_grammaire: str | None = "PAROLE_GENERIQUE",
) -> Paragraphe:
    return Paragraphe(
        uid=uid,
        texte=text,
        dossierRefUid=dossier_uid,
        pointOdjRefUid=point_uid,
        valeurPtsOdj=value,
        acteurRefUid=actor_uid,
        orateur=orateur,
        ordreAbsoluSeance=order,
        debatRefUid="DEB1",
        reunionRefUid="R1",
        dateSeance=datetime(2026, 1, 5, tzinfo=timezone.utc),
        estPresident=president,
        sommaire=sommaire,
        roleDebat=role,
        codeGrammaire=code_grammaire,
    )


def test_filter_debat_interventions_prefers_dossier_ref_then_point_then_order():
    paragraphes = [
        intervention("I1", "hors dossier", dossier_uid="D2", point_uid="P2", value="2"),
        intervention("I2", "bon dossier", dossier_uid="D1", point_uid="P9", value="9"),
        intervention("I3", "bon point", dossier_uid=None, point_uid="P1", value="1"),
    ]

    point = PointOdj(uid="P1", dossierLegislatifUid="D1", ordrePoint=1)

    assert [item.uid for item in filter_debat_interventions(paragraphes, "D1", point, 1)] == ["I2"]

    without_dossier_refs = [item.model_copy(update={"dossierRefUid": None}) for item in paragraphes]
    assert [
        item.uid for item in filter_debat_interventions(without_dossier_refs, "D1", point, 1)
    ] == ["I3"]

    without_point_refs = [
        item.model_copy(update={"dossierRefUid": None, "pointOdjRefUid": None})
        for item in paragraphes
    ]
    assert [
        item.uid for item in filter_debat_interventions(without_point_refs, "D1", point, 1)
    ] == ["I3"]


def test_point_is_valid_for_dossier_ignores_cancelled_or_unrelated_points():
    assert point_is_valid_for_dossier(
        PointOdj(uid="P1", dossierLegislatifUid="D1", etat="Confirmé"),
        "D1",
    )
    assert not point_is_valid_for_dossier(
        PointOdj(uid="P2", dossierLegislatifUid="D1", etat="Supprimé"),
        "D1",
    )
    assert not point_is_valid_for_dossier(
        PointOdj(uid="P3", dossierLegislatifUid="D2", etat="Confirmé"),
        "D1",
    )


def test_split_intervention_lanes_keeps_speech_stats_clean_and_context_available():
    interventions = [
        intervention(
            "SPEECH",
            "Je soutiens cette proposition.",
            actor_uid="A1",
            orateur="Alice Martin",
            code_grammaire="PAROLE_GENERIQUE",
        ),
        intervention(
            "TITLE",
            "Article 1er",
            actor_uid=None,
            orateur=None,
            code_grammaire="DISC_ARTICLES_2_4",
        ),
        intervention(
            "NOISE",
            "Baratin !",
            actor_uid=None,
            orateur=None,
            code_grammaire="INTERRUPTION_1_10",
        ),
    ]

    speech, procedure_events, interruptions = split_intervention_lanes(interventions)

    assert [item.uid for item in speech] == ["SPEECH"]
    assert procedure_events[0].text == "Article 1er"
    assert interruptions[0].text == "Baratin !"


class FakeClient:
    def __init__(self):
        self.actor_fetch_count = 0
        self.actor = Acteur(
            uid="A1",
            prenom="Alice",
            nom="Martin",
            groupeParlementaireUid="G1",
            groupeParlementaire=Organe(uid="G1", libelleAbrev="SOC", libelle="Socialistes"),
            circonscription=Organe(uid="C1", libelle="Paris 1re circonscription"),
            mandatPrincipal=Mandat(uid="M1", libQualite="Députée"),
        )

    def get_reunion(self, uid: str, include=None):
        assert include == ["pointsOdj"]
        return Agenda(
            uid=uid,
            compteRenduRefUid="DEB1",
            dateSeance=datetime(2026, 1, 5, tzinfo=timezone.utc),
            numSeanceJO="42",
            pointsOdj=[
                PointOdj(uid="P0", dossierLegislatifUid="D9", ordrePoint=1, etat="Confirmé"),
                PointOdj(uid="P1", dossierLegislatifUid="D1", ordrePoint=2, etat="Confirmé"),
            ],
        )

    def get_debat(self, uid: str, include=None):
        assert include == ["paragraphes"]
        return Debat(
            uid=uid,
            paragraphes=[
                intervention(
                    "I1",
                    "Intervention importante sur le fond du texte.",
                    order=2,
                    sommaire="Discussion générale",
                    code_grammaire="PAROLE_GENERIQUE",
                ),
                intervention("I2", "Intervention d'un autre dossier.", dossier_uid="D9", order=1),
                intervention(
                    "I3",
                    "Article 1er",
                    actor_uid=None,
                    orateur=None,
                    order=3,
                    code_grammaire="DISC_ARTICLES_2_4",
                ),
                intervention(
                    "I4",
                    "Brouhaha sur plusieurs bancs.",
                    actor_uid=None,
                    orateur=None,
                    order=4,
                    code_grammaire="INTERRUPTION_1_10",
                ),
            ],
        )

    def get_acteur(self, uid: str, include=None):
        assert include == ["groupeParlementaire", "circonscription", "mandatPrincipal"]
        self.actor_fetch_count += 1
        return self.actor


def test_build_debat_discussion_packs_locates_and_enriches_speakers():
    get_actor_cached.cache_clear()
    client = FakeClient()
    dossier = Dossier(
        uid="D1",
        titre="Projet de loi test",
        chambre=ChambreEnum.AN,
        actesLegislatifs=[
            ActeLegislatif(
                uid="ACTE1",
                codeActe="AN1-DEBATS-SEANCE",
                reunionRefUid="R1",
                pointOdjUid="P1",
                dateActe=datetime(2026, 1, 5, tzinfo=timezone.utc),
            )
        ],
        pointsOdj=[
            PointOdj(
                uid="P1",
                dossierLegislatifUid="D1",
                agendaRefUid="R1",
                ordrePoint=2,
                etat="Confirmé",
                objet="Suite de la discussion",
                typePointOdj="Discussion",
            ),
            PointOdj(
                uid="P2",
                dossierLegislatifUid="D1",
                agendaRefUid="R2",
                etat="Supprimé",
            ),
        ],
    )

    packs = build_debat_discussion_packs(dossier, client, max_intervention_chars=20)

    assert len(packs) == 1
    pack = packs[0]
    assert pack.discussion_uid == "D1:R1:DEB1:P1"
    assert pack.input_truncated
    assert pack.original_intervention_count == 3
    assert pack.interventions[0].text == "Intervention importa"
    assert pack.speakers[0].display_name == "Alice Martin"
    assert pack.speakers[0].acteur.uid == "A1"
    assert pack.speakers[0].acteur.nom == "Alice Martin"
    assert pack.speakers[0].groupe == "SOC"
    assert pack.speakers[0].groupe_ref.uid == "G1"
    assert pack.speakers[0].groupe_ref.libelle == "SOC"
    assert pack.speakers[0].circonscription == "Paris 1re circonscription"
    assert pack.intervenants_stats[0].word_count == 7
    assert pack.groupes_stats[0].groupe.uid == "G1"
    assert pack.groupes_stats[0].intervention_count == 1
    assert pack.groupes_stats[0].participation == "forte"
    assert pack.sommaire_source[0].titre == "Discussion générale"
    assert len(pack.speakers) == 1
    assert len(pack.intervenants_stats) == 1
    assert len(pack.interventions) == 1
    assert pack.procedure_events[0].code_grammaire == "DISC_ARTICLES_2_4"
    assert pack.interruptions[0].code_grammaire == "INTERRUPTION_1_10"
    assert client.actor_fetch_count == 1

    build_debat_discussion_packs(dossier, client, max_intervention_chars=20)

    assert client.actor_fetch_count == 1


class MissingDebatClient:
    def get_reunion(self, uid: str, include=None):
        return None

    def get_interventions(self, page: int, per_page: int, dossierRefUid: str):
        raise AssertionError(
            "The séance debate locator must not fallback to dossier interventions."
        )

    def get_acteur(self, uid: str, include=None):
        return None


def test_build_debat_discussion_packs_returns_empty_when_main_path_fails():
    dossier = Dossier(
        uid="D1",
        actesLegislatifs=[
            ActeLegislatif(
                uid="ACTE1",
                codeActe="AN1-DEBATS-SEANCE",
                reunionRefUid="R1",
                pointOdjUid="P1",
            )
        ],
    )

    packs = build_debat_discussion_packs(dossier, MissingDebatClient(), per_page=10)

    assert packs == []


def test_build_debat_discussion_packs_ignores_commission_odj_without_seance_acte():
    dossier = Dossier(
        uid="D1",
        pointsOdj=[PointOdj(uid="P1", dossierLegislatifUid="D1", agendaRefUid="R1")],
        actesLegislatifs=[
            ActeLegislatif(
                uid="ACTE1",
                codeActe="AN1-COM-FOND-REUNION",
                reunionRefUid="R1",
                pointOdjUid="P1",
            )
        ],
    )

    packs = build_debat_discussion_packs(dossier, MissingDebatClient(), per_page=10)

    assert packs == []


def test_build_speaker_context_keeps_stats_for_all_but_president_not_substantive():
    interventions = [
        intervention(
            "PRES",
            "La séance est ouverte.",
            actor_uid="PRES",
            orateur="La présidente",
            president=True,
            code_grammaire="PAROLE_GENERIQUE",
        ),
        intervention(
            "A1",
            "Je défends cet amendement car il modifie concrètement l'article examiné.",
            actor_uid="A1",
            code_grammaire="PAROLE_GENERIQUE",
        ),
    ]

    _, stats = build_speaker_context(interventions, actors={})

    president_stat = next(item for item in stats if item.speaker_id == "PRES")
    assert president_stat.word_count == 4
    assert president_stat.substantive_word_count == 0
    assert president_stat.is_president


def test_build_speaker_context_computes_group_participation_stats():
    actor = Acteur(
        uid="A1",
        prenom="Alice",
        nom="Martin",
        groupeParlementaireUid="G1",
        groupeParlementaire=Organe(uid="G1", libelleAbrev="SOC"),
    )
    interventions = [
        intervention("I1", "Un deux trois quatre cinq six sept huit neuf dix.", actor_uid="A1"),
        intervention("I2", "Un deux.", actor_uid=None, orateur="Sans groupe"),
    ]

    _, stats = build_speaker_context(interventions, actors={"A1": actor})

    alice = next(item for item in stats if item.speaker_id == "A1")
    assert alice.acteur.uid == "A1"
    assert alice.groupe_ref.uid == "G1"
