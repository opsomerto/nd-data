"""Build compact, typed input packs for debate summary agents."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache
from weakref import WeakValueDictionary

from nd_data.debat_summary.config import SETTINGS
from nd_data.debat_summary.models import (
    ActorRef,
    DebateContextEvent,
    DebatDiscussionInputPack,
    DiscussionOutlineItem,
    DiscussionSourceRefs,
    GroupRef,
    GroupStat,
    InterventionExcerpt,
    SpeakerSnapshot,
    SpeakerStat,
)
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import (
    ActeLegislatif,
    Acteur,
    Agenda,
    Debat,
    Dossier,
    Mandat,
    Paragraphe,
    PointOdj,
)

VALID_ODJ_STATES = {"Confirmé", "Eventuel", None}
INVALID_ODJ_STATES = {"Annulé", "Annule", "Supprimé", "Supprime"}
MIN_SUBSTANTIVE_WORDS = 10
ACTOR_INCLUDES = ["groupeParlementaire", "circonscription", "mandatPrincipal"]
CLIENT_REGISTRY: WeakValueDictionary[int, TricoteuseAPIClient] = WeakValueDictionary()
MEANINGFUL_SPEECH_CODES = {"PAROLE_GENERIQUE"}
MAX_CONTEXT_EVENTS = 80
MAX_INTERRUPTION_EVENTS = 40
MAX_CONTEXT_EVENT_CHARS = 500


def value(obj, field: str):
    item = getattr(obj, field, None)
    return getattr(item, "value", item)


def point_is_valid_for_dossier(point: PointOdj, dossier_uid: str) -> bool:
    if not point.uid or point.dossierLegislatifUid != dossier_uid:
        return False
    if point.etat in INVALID_ODJ_STATES:
        return False
    return point.etat in VALID_ODJ_STATES or point.etat not in INVALID_ODJ_STATES


def is_seance_debate_acte(acte: ActeLegislatif) -> bool:
    code = (acte.codeActe or "").upper()
    return "DEBATS-SEANCE" in code


def seance_debate_actes(dossier: Dossier) -> list[ActeLegislatif]:
    return sorted(
        [acte for acte in (dossier.actesLegislatifs or []) if is_seance_debate_acte(acte)],
        key=lambda acte: (
            acte.dateActe is None,
            acte.dateActe or datetime.min,
            acte.uid or "",
        ),
    )


def find_point_for_acte(
    acte: ActeLegislatif,
    dossier: Dossier,
    reunion: Agenda | None,
) -> PointOdj | None:
    if not acte.pointOdjUid:
        return None
    for point in (reunion.pointsOdj if reunion else None) or []:
        if point.uid == acte.pointOdjUid:
            return point
    for point in dossier.pointsOdj or []:
        if point.uid == acte.pointOdjUid:
            return point
    return PointOdj(uid=acte.pointOdjUid, dossierLegislatifUid=dossier.uid)


def active_point_order(reunion: Agenda | None, point: PointOdj) -> int | None:
    if point.ordrePoint is not None:
        return point.ordrePoint
    if not reunion or not reunion.pointsOdj:
        return None
    order = 1
    for item in reunion.pointsOdj:
        if item.etat in INVALID_ODJ_STATES:
            continue
        if item.uid == point.uid:
            return order
        order += 1
    return None


def sort_interventions(interventions: Iterable[Paragraphe]) -> list[Paragraphe]:
    return sorted(
        interventions,
        key=lambda item: (
            item.ordreAbsoluSeance is None,
            item.ordreAbsoluSeance or 0,
            item.uid or "",
        ),
    )


def filter_debat_interventions(
    paragraphes: list[Paragraphe],
    dossier_uid: str,
    point: PointOdj | None = None,
    ordre_point: int | None = None,
) -> list[Paragraphe]:
    by_dossier = [item for item in paragraphes if item.dossierRefUid == dossier_uid]
    if by_dossier:
        return sort_interventions(by_dossier)

    if point and point.uid:
        by_point = [item for item in paragraphes if item.pointOdjRefUid == point.uid]
        if by_point:
            return sort_interventions(by_point)

    if ordre_point is not None:
        by_order = [item for item in paragraphes if item.valeurPtsOdj == str(ordre_point)]
        if by_order:
            return sort_interventions(by_order)

    return []


def actor_display_name(actor: Acteur | None, fallback: str | None, actor_uid: str | None) -> str:
    if actor and (actor.prenom or actor.nom):
        return " ".join(part for part in (actor.prenom, actor.nom) if part)
    if fallback:
        return fallback
    return actor_uid or "Intervenant non identifié"


def actor_ref(actor: Acteur | None, fallback: str | None, actor_uid: str | None) -> ActorRef:
    return ActorRef(
        uid=actor.uid if actor else actor_uid, nom=actor_display_name(actor, fallback, actor_uid)
    )


def group_label(actor: Acteur | None) -> str | None:
    group = actor.groupeParlementaire if actor else None
    if not group:
        return actor.groupeParlementaireUid if actor else None
    return group.libelleAbrev or group.libelle or group.uid


def group_ref(actor: Acteur | None) -> GroupRef | None:
    if not actor:
        return None
    group = actor.groupeParlementaire
    label = group_label(actor)
    if not label:
        return None
    return GroupRef(
        uid=actor.groupeParlementaireUid or (group.uid if group else None), libelle=label
    )


def circonscription_label(actor: Acteur | None) -> str | None:
    if not actor:
        return None
    circo = actor.circonscription
    if circo:
        label = circo.libelle or circo.libelleAbrev
        if label:
            return label
        if circo.numDepartement is not None and circo.numCirco is not None:
            return f"{circo.numDepartement}-{circo.numCirco}"
    mandat = actor.mandatPrincipal
    if mandat:
        return mandate_circonscription_label(mandat)
    return None


def mandate_circonscription_label(mandat: Mandat) -> str | None:
    parts = []
    if mandat.departement:
        parts.append(mandat.departement)
    if mandat.numCirco is not None:
        parts.append(f"{mandat.numCirco}e circonscription")
    return ", ".join(parts) if parts else mandat.refCirconscription


def speaker_id_for(intervention: Paragraphe) -> str:
    if intervention.acteurRefUid:
        return intervention.acteurRefUid
    if intervention.orateur:
        return f"orateur:{intervention.orateur}"
    return f"unknown:{intervention.uid or intervention.ordreAbsoluSeance or 'speaker'}"


def role_flags(role: str | None, orateur: str | None) -> tuple[bool, bool]:
    text = " ".join(part for part in (role, orateur) if part).lower()
    return "ministre" in text or "gouvernement" in text, "rapporteur" in text


def is_substantive(intervention: Paragraphe) -> bool:
    if not intervention.texte:
        return False
    if intervention.estPresident:
        text = intervention.texte.lower()
        policy_terms = ("article", "amendement", "avis", "projet", "proposition", "texte")
        if not any(term in text for term in policy_terms):
            return False
    return len(intervention.texte.split()) >= MIN_SUBSTANTIVE_WORDS


def code_grammaire(intervention: Paragraphe) -> str | None:
    return intervention.codeGrammaire


def is_speech_intervention(intervention: Paragraphe) -> bool:
    return (
        code_grammaire(intervention) in MEANINGFUL_SPEECH_CODES
        and bool(intervention.texte)
        and bool(intervention.acteurRefUid or intervention.orateur)
    )


def is_interruption(intervention: Paragraphe) -> bool:
    return (code_grammaire(intervention) or "").startswith("INTERRUPTION")


def debate_context_event(intervention: Paragraphe) -> DebateContextEvent | None:
    text = (
        intervention.texte or intervention.sommaire or intervention.structure or intervention.valeur
    )
    if not text:
        return None
    return DebateContextEvent(
        ordre=intervention.ordreAbsoluSeance,
        code_grammaire=intervention.codeGrammaire,
        text=text[:MAX_CONTEXT_EVENT_CHARS],
        article=intervention.art,
        type_debat=intervention.typeDebat,
    )


def split_intervention_lanes(
    interventions: list[Paragraphe],
) -> tuple[list[Paragraphe], list[DebateContextEvent], list[DebateContextEvent]]:
    speech = []
    procedure_events = []
    interruptions = []

    for intervention in interventions:
        if is_speech_intervention(intervention):
            speech.append(intervention)
            continue
        event = debate_context_event(intervention)
        if not event:
            continue
        if is_interruption(intervention):
            interruptions.append(event)
        else:
            procedure_events.append(event)

    return (
        sort_interventions(speech),
        procedure_events[:MAX_CONTEXT_EVENTS],
        interruptions[:MAX_INTERRUPTION_EVENTS],
    )


def build_actor_cache(
    interventions: list[Paragraphe],
    client: TricoteuseAPIClient | None,
) -> dict[str, Acteur]:
    if client is None:
        return {}
    client_key = register_client(client)
    actors = {}
    for actor_uid in sorted({item.acteurRefUid for item in interventions if item.acteurRefUid}):
        actor = get_actor_cached(client_key, actor_uid)
        if actor:
            actors[actor_uid] = actor
    return actors


def register_client(client: TricoteuseAPIClient) -> int:
    client_key = id(client)
    CLIENT_REGISTRY[client_key] = client
    return client_key


@lru_cache(maxsize=None)
def get_actor_cached(client_key: int, actor_uid: str) -> Acteur | None:
    client = CLIENT_REGISTRY.get(client_key)
    if client is None:
        return None
    try:
        return client.get_acteur(actor_uid, include=ACTOR_INCLUDES)
    except Exception:
        return None


def build_speaker_context(
    interventions: list[Paragraphe],
    actors: dict[str, Acteur],
) -> tuple[list[SpeakerSnapshot], list[SpeakerStat]]:
    snapshots: dict[str, SpeakerSnapshot] = {}
    stats: dict[str, SpeakerStat] = {}

    for intervention in interventions:
        speaker_id = speaker_id_for(intervention)
        actor = actors.get(intervention.acteurRefUid or "")
        display_name = actor_display_name(actor, intervention.orateur, intervention.acteurRefUid)
        is_government, is_rapporteur = role_flags(intervention.roleDebat, intervention.orateur)
        is_president = bool(intervention.estPresident)
        groupe = group_label(actor)
        actor_reference = actor_ref(actor, intervention.orateur, intervention.acteurRefUid)
        group_reference = group_ref(actor)

        snapshot = snapshots.get(speaker_id)
        if snapshot is None:
            snapshots[speaker_id] = SpeakerSnapshot(
                speaker_id=speaker_id,
                acteur_uid=intervention.acteurRefUid,
                display_name=display_name,
                acteur=actor_reference,
                groupe_uid=actor.groupeParlementaireUid if actor else None,
                groupe=groupe,
                groupe_ref=group_reference,
                circonscription=circonscription_label(actor),
                mandat=actor.mandatPrincipal.libQualite
                if actor and actor.mandatPrincipal
                else None,
                role=intervention.roleDebat,
                is_president=is_president,
                is_government=is_government,
                is_rapporteur=is_rapporteur,
            )
        else:
            snapshot.is_president = snapshot.is_president or is_president
            snapshot.is_government = snapshot.is_government or is_government
            snapshot.is_rapporteur = snapshot.is_rapporteur or is_rapporteur
            snapshot.role = snapshot.role or intervention.roleDebat

        word_count = len((intervention.texte or "").split())
        stat = stats.get(speaker_id)
        if stat is None:
            stats[speaker_id] = SpeakerStat(
                speaker_id=speaker_id,
                acteur_uid=intervention.acteurRefUid,
                display_name=display_name,
                acteur=actor_reference,
                groupe_ref=group_reference,
                groupe=groupe,
                intervention_count=1,
                word_count=word_count,
                substantive_word_count=word_count if is_substantive(intervention) else 0,
                is_president=is_president,
                is_government=is_government,
                is_rapporteur=is_rapporteur,
            )
        else:
            stat.intervention_count += 1
            stat.word_count += word_count
            if is_substantive(intervention):
                stat.substantive_word_count += word_count
            stat.is_president = stat.is_president or is_president
            stat.is_government = stat.is_government or is_government
            stat.is_rapporteur = stat.is_rapporteur or is_rapporteur

    ordered_stats = sorted(
        stats.values(),
        key=lambda item: (-item.substantive_word_count, -item.word_count, item.display_name),
    )
    ordered_snapshots = sorted(snapshots.values(), key=lambda item: item.display_name)
    return ordered_snapshots, ordered_stats


def participation_level(word_count: int, total_words: int) -> str:
    if total_words <= 0:
        return "indetermine"
    share = word_count / total_words
    if share >= 0.25:
        return "forte"
    if share >= 0.08:
        return "moderee"
    return "faible"


def build_group_stats(speaker_stats: list[SpeakerStat]) -> list[GroupStat]:
    groups: dict[str, GroupStat] = {}
    total_words = sum(stat.word_count for stat in speaker_stats)

    for stat in speaker_stats:
        group_reference = stat.groupe_ref
        group_key = (
            group_reference.uid or group_reference.libelle if group_reference else "sans_groupe"
        )
        group = groups.get(group_key)
        if group is None:
            group = GroupStat(
                group_key=group_key,
                groupe=group_reference,
                speaker_count=0,
                intervention_count=0,
                word_count=0,
                substantive_word_count=0,
                speaker_ids=[],
            )
            groups[group_key] = group
        group.speaker_count += 1
        group.intervention_count += stat.intervention_count
        group.word_count += stat.word_count
        group.substantive_word_count += stat.substantive_word_count
        group.speaker_ids.append(stat.speaker_id)

    for group in groups.values():
        group.participation = participation_level(group.word_count, total_words)

    return sorted(
        groups.values(),
        key=lambda item: (-item.word_count, item.groupe.libelle if item.groupe else item.group_key),
    )


def build_sommaire_source(interventions: list[Paragraphe]) -> list[DiscussionOutlineItem]:
    seen = set()
    items = []
    for intervention in interventions:
        title = intervention.sommaire or intervention.structure or intervention.valeur
        if not title:
            continue
        key = (
            title.strip(),
            intervention.typeDebat,
            intervention.structure,
            intervention.art,
            intervention.ordreAbsoluSeance,
        )
        if key in seen:
            continue
        seen.add(key)
        items.append(
            DiscussionOutlineItem(
                ordre=intervention.ordreAbsoluSeance,
                titre=title.strip(),
                speaker_id=speaker_id_for(intervention),
                type_debat=intervention.typeDebat,
                structure=intervention.structure,
                article=intervention.art,
            )
        )
    return items[:20]


def cap_interventions(
    interventions: list[Paragraphe],
    max_chars: int,
) -> tuple[list[InterventionExcerpt], int, int, bool]:
    original_chars = sum(len(item.texte or "") for item in interventions)
    remaining = max(0, max_chars)
    excerpts = []
    truncated = False

    for intervention in interventions:
        text = intervention.texte or ""
        if not text:
            continue
        if remaining <= 0:
            truncated = True
            break
        kept_text = text[:remaining]
        if len(kept_text) < len(text):
            truncated = True
        excerpts.append(
            InterventionExcerpt(
                uid=intervention.uid,
                speaker_id=speaker_id_for(intervention),
                ordre=intervention.ordreAbsoluSeance,
                text=kept_text,
                role=intervention.roleDebat,
                is_president=bool(intervention.estPresident),
            )
        )
        remaining -= len(kept_text)

    input_chars = sum(len(item.text) for item in excerpts)
    return excerpts, original_chars, input_chars, truncated


def discussion_uid(
    dossier_uid: str,
    reunion_uid: str | None,
    debat_uid: str | None,
    point_odj_uid: str | None,
    valeur_pts_odj: str | None = None,
) -> str:
    parts = [dossier_uid, reunion_uid or "reunion", debat_uid or "debat"]
    parts.append(point_odj_uid or f"odj-{valeur_pts_odj or 'unknown'}")
    return ":".join(parts)


def build_discussion_pack(
    dossier: Dossier,
    interventions: list[Paragraphe],
    client: TricoteuseAPIClient | None,
    reunion: Agenda | None = None,
    debat: Debat | None = None,
    point: PointOdj | None = None,
    max_intervention_chars: int = SETTINGS.max_intervention_chars,
) -> DebatDiscussionInputPack | None:
    if not dossier.uid or not interventions:
        return None

    speech_interventions, procedure_events, interruptions = split_intervention_lanes(interventions)
    actors = build_actor_cache(speech_interventions, client)
    speakers, stats = build_speaker_context(speech_interventions, actors)
    group_stats = build_group_stats(stats)
    sommaire_source = build_sommaire_source(interventions)
    excerpts, original_chars, input_chars, truncated = cap_interventions(
        speech_interventions,
        max_intervention_chars,
    )
    ordre_point = active_point_order(reunion, point) if point else None
    debat_uid = debat.uid if debat else interventions[0].debatRefUid
    reunion_uid = reunion.uid if reunion else interventions[0].reunionRefUid
    point_uid = point.uid if point else interventions[0].pointOdjRefUid
    source_refs = DiscussionSourceRefs(
        dossier_uid=dossier.uid,
        debat_uid=debat_uid,
        reunion_uid=reunion_uid,
        point_odj_uid=point_uid,
    )

    return DebatDiscussionInputPack(
        dossier_uid=dossier.uid,
        dossier_titre=dossier.titre,
        chambre=value(dossier, "chambre"),
        legislature=dossier.legislature,
        type_initiative=dossier.typeInitiative,
        type_procedure=dossier.typeProcedure,
        statut=dossier.statut,
        discussion_uid=discussion_uid(
            dossier.uid,
            reunion_uid,
            debat_uid,
            point_uid,
            interventions[0].valeurPtsOdj,
        ),
        debat_uid=debat_uid,
        reunion_uid=reunion_uid,
        point_odj_uid=point_uid,
        date_seance=(reunion.dateSeance if reunion else None) or interventions[0].dateSeance,
        objet=point.objet if point else None,
        type_point_odj=point.typePointOdj if point else None,
        ordre_point=ordre_point,
        num_seance_jo=reunion.numSeanceJO if reunion else None,
        quantieme=reunion.quantieme if reunion else None,
        speakers=speakers,
        intervenants_stats=stats,
        groupes_stats=group_stats,
        sommaire_source=sommaire_source,
        interventions=excerpts,
        procedure_events=procedure_events,
        interruptions=interruptions,
        source_refs=source_refs,
        original_intervention_count=len(interventions),
        original_text_chars=original_chars,
        input_text_chars=input_chars,
        input_truncated=truncated,
    )


def build_debat_discussion_packs(
    dossier: Dossier,
    client: TricoteuseAPIClient,
    per_page: int = 100,
    max_intervention_chars: int = SETTINGS.max_intervention_chars,
) -> list[DebatDiscussionInputPack]:
    packs = []
    actes = seance_debate_actes(dossier)
    seen_reunions = set()

    for acte in actes:
        if not acte.reunionRefUid or acte.reunionRefUid in seen_reunions:
            continue
        seen_reunions.add(acte.reunionRefUid)
        try:
            reunion = client.get_reunion(acte.reunionRefUid, include=["pointsOdj"])
            if not reunion:
                continue
            debat_uid = reunion.compteRenduRefUid
            if not debat_uid and reunion.compteRenduRef:
                debat_uid = reunion.compteRenduRef[0].uid
            if not debat_uid:
                # TODO: log this
                continue
            debat = client.get_debat(debat_uid, include=["paragraphes"])
            if not debat or not debat.paragraphes:
                continue
            point = find_point_for_acte(acte, dossier, reunion)
            ordre_point = active_point_order(reunion, point) if point else None
            interventions = filter_debat_interventions(
                debat.paragraphes,
                dossier.uid,
                point,
                ordre_point,
            )
            pack = build_discussion_pack(
                dossier,
                interventions,
                client,
                reunion=reunion,
                debat=debat,
                point=point,
                max_intervention_chars=max_intervention_chars,
            )
            if pack:
                packs.append(pack)
        except Exception:
            continue

    return dedupe_packs(packs)


def dedupe_packs(packs: list[DebatDiscussionInputPack]) -> list[DebatDiscussionInputPack]:
    seen = set()
    deduped = []
    for pack in sorted(
        packs,
        key=lambda item: (
            item.date_seance or datetime.min,
            item.reunion_uid or "",
            item.ordre_point or 0,
            item.discussion_uid,
        ),
    ):
        if pack.discussion_uid in seen:
            continue
        seen.add(pack.discussion_uid)
        deduped.append(pack)
    return deduped


def locate_debat_discussion_packs(
    client: TricoteuseAPIClient,
    dossier_uid: str,
    per_page: int = 100,
    max_intervention_chars: int = SETTINGS.max_intervention_chars,
) -> list[DebatDiscussionInputPack]:
    dossier = client.get_dossier(dossier_uid, include=["pointsOdj", "actesLegislatifs"])
    if not dossier:
        return []
    return build_debat_discussion_packs(
        dossier,
        client,
        per_page=per_page,
        max_intervention_chars=max_intervention_chars,
    )
