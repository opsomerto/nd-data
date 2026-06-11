"""Pydantic AI agents for debate summary modules."""

import json
import os
from datetime import datetime, timezone

import dotenv
from pydantic_ai import Agent

from nd_data.debat_summary.config import SETTINGS
from nd_data.debat_summary.models import (
    ActorRef,
    CumulativeDebatInputPack,
    CumulativeDebatSynthesis,
    CumulativeGroupSynthesis,
    DebateDynamics,
    DebatDiscussionEnrichment,
    DebatDiscussionInputPack,
    DebatDiscussionSummary,
    GroupRef,
    GroupSynthesis,
    GroupStat,
    LLMCumulativeDebatSynthesis,
    LLMDebatDiscussionSummary,
    SpeakerPosition,
)

dotenv.load_dotenv()

AGENT_VERSION = "1.0"
DEFAULT_MODEL = SETTINGS.model


COMMON_RULES = """
Tu es un assistant expert en analyse des débats parlementaires français.
Réponds en français, pour un public non spécialiste.
Reste neutre, factuel et synthétique.
Utilise uniquement les interventions et métadonnées fournies.
N'invente pas de position politique: si une prise de position n'est pas claire, indique `indetermine`.
Sépare les prises de position de fond des rappels de procédure, interruptions et présidences de séance.
"""


DISCUSSION_PROMPT = (
    COMMON_RULES
    + """
Mission: produire un résumé compact d'une discussion en séance sur un dossier législatif.

Contraintes:
- `resume`: 3 à 6 phrases maximum.
- `sommaire`: jusqu'à 8 étapes décrivant le déroulé concret du débat
  (présentation, discussion générale, article, amendements, explications, etc.). Appuie-toi
  sur `sommaire_source` quand il est disponible, sinon déduis prudemment du texte.
- `sujets`: jusqu'à 6 thèmes débattus, avec consensus et tensions quand ils existent.
- `positions`: jusqu'à 10 intervenants importants, surtout ceux qui
  ont des interventions substantielles. Utilise `speaker_id` pour identifier l'intervenant.
  Ne remplis pas artificiellement.
- `groupes`: synthèse courte par groupe politique ou rôle institutionnel identifiable.
  Utilise les statistiques de groupe fournies pour contextualiser la synthèse, mais ne recopie
  pas les statistiques.
- `dynamique`: qualifie le ton général, le niveau de conflit, le caractère technique,
  idéologique ou procédural, et les moments marquants.

Règles importantes:
- `interventions` contient les prises de parole attribuées à des intervenants; appuie les
  positions sur ces éléments.
- `procedure_events` contient les titres, articles, votes, retraits/adoptions d'amendements,
  suspensions et autres descriptions de séance; utilise-les pour le sommaire et le contexte,
  pas comme prises de position.
- `interruptions` contient des interruptions ou exclamations non attribuées; utilise-les
  seulement pour qualifier le ton, la tension ou le désordre du débat.
- Ne déduis pas la position d'un groupe uniquement à partir de son étiquette.
- Un président de séance ne doit être résumé comme intervenant de fond que s'il développe
  réellement un argument sur le texte.
- Les ministres, rapporteurs et auteurs peuvent être mentionnés par rôle quand c'est utile.
- Si le débat est essentiellement procédural ou tronqué, dis-le sobrement.
"""
)


CUMULATIVE_PROMPT = (
    COMMON_RULES
    + """
Mission: synthétiser l'ensemble des débats en séance déjà résumés pour un même dossier.

Tu ne reçois pas les interventions brutes: seulement les résumés structurés de chaque discussion.
Fais ressortir:
- l'évolution des positions;
- les sujets récurrents;
- les lignes de clivage;
- les nuances par groupe;
- les moments marquants.

Évite de répéter séance par séance. Le résultat doit aider à comprendre la dynamique globale.
"""
)


discussion_agent: Agent[None, LLMDebatDiscussionSummary] = Agent(
    output_type=LLMDebatDiscussionSummary,
    system_prompt=DISCUSSION_PROMPT.strip(),
    defer_model_check=True,
)

cumulative_agent: Agent[None, LLMCumulativeDebatSynthesis] = Agent(
    output_type=LLMCumulativeDebatSynthesis,
    system_prompt=CUMULATIVE_PROMPT.strip(),
    defer_model_check=True,
)


def pack_prompt(pack: DebatDiscussionInputPack | CumulativeDebatInputPack) -> str:
    payload = pack.model_dump(mode="json", exclude_none=True)
    return (
        "Voici les données à analyser, au format JSON compact. "
        "Utilise uniquement ces informations.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def run_discussion_summary(
    pack: DebatDiscussionInputPack,
    model_name: str = DEFAULT_MODEL,
) -> DebatDiscussionSummary:
    result = discussion_agent.run_sync(pack_prompt(pack), model=model_name)
    return discussion_summary_from_llm(result.output)


def run_cumulative_synthesis(
    pack: CumulativeDebatInputPack,
    model_name: str = DEFAULT_MODEL,
) -> CumulativeDebatSynthesis:
    result = cumulative_agent.run_sync(pack_prompt(pack), model=model_name)
    output = result.output
    return CumulativeDebatSynthesis(
        resume_global=output.resume_global,
        evolution_positions=output.evolution_positions,
        sujets_recurrents=output.sujets_recurrents[:8],
        lignes_de_clivage=output.lignes_de_clivage[:8],
        groupes=[CumulativeGroupSynthesis(**group.model_dump()) for group in output.groupes],
        moments_marquants=output.moments_marquants[:8],
        dossier_uid=pack.dossier_uid,
        debate_summary_refs=[summary.discussion_uid for summary in pack.discussion_summaries],
        model_name=model_name,
        agent_version=AGENT_VERSION,
        processed_at=datetime.now(tz=timezone.utc),
    )


def discussion_summary_from_llm(output: LLMDebatDiscussionSummary) -> DebatDiscussionSummary:
    return DebatDiscussionSummary(
        resume_court=output.resume_court,
        sommaire_discussion=output.sommaire_discussion[:8],
        sujets=output.sujets[:6],
        positions_intervenants_principaux=[
            SpeakerPosition(**position.model_dump())
            for position in output.positions_intervenants_principaux[:10]
        ],
        synthese_groupes=[
            GroupSynthesis(**group.model_dump()) for group in output.synthese_groupes
        ],
        dynamique_debat=DebateDynamics(**output.dynamique_debat.model_dump()),
    )


def normalize_label(value: str | None) -> str:
    return (value or "").casefold().strip()


def group_stat_lookup(group_stats: list[GroupStat]) -> dict[str, GroupStat]:
    lookup = {}
    for stat in group_stats:
        lookup[normalize_label(stat.group_key)] = stat
        if stat.groupe:
            lookup[normalize_label(stat.groupe.uid)] = stat
            lookup[normalize_label(stat.groupe.libelle)] = stat
    return {key: value for key, value in lookup.items() if key}


def speaker_lookup(pack: DebatDiscussionInputPack) -> dict[str, tuple[ActorRef, GroupRef | None]]:
    lookup = {}
    for speaker in pack.speakers:
        lookup[speaker.speaker_id] = (speaker.acteur, speaker.groupe_ref)
        lookup[normalize_label(speaker.display_name)] = (speaker.acteur, speaker.groupe_ref)
    for stat in pack.intervenants_stats:
        lookup.setdefault(stat.speaker_id, (stat.acteur, stat.groupe_ref))
        lookup.setdefault(normalize_label(stat.display_name), (stat.acteur, stat.groupe_ref))
    return {key: value for key, value in lookup.items() if key}


def attach_computed_refs(
    summary: DebatDiscussionSummary,
    pack: DebatDiscussionInputPack,
) -> DebatDiscussionSummary:
    group_lookup = group_stat_lookup(pack.groupes_stats)
    speaker_refs = speaker_lookup(pack)

    for group_summary in summary.synthese_groupes:
        stat = None
        if group_summary.groupe_ref:
            stat = group_lookup.get(normalize_label(group_summary.groupe_ref.uid))
            stat = stat or group_lookup.get(normalize_label(group_summary.groupe_ref.libelle))
        stat = stat or group_lookup.get(normalize_label(group_summary.groupe))
        if stat:
            group_summary.stats = stat
            group_summary.participation = stat.participation
            group_summary.groupe_ref = group_summary.groupe_ref or stat.groupe

    for position in summary.positions_intervenants_principaux:
        refs = speaker_refs.get(position.speaker_id) or speaker_refs.get(
            normalize_label(position.nom)
        )
        if refs:
            position.acteur = position.acteur or refs[0]
            position.groupe_ref = position.groupe_ref or refs[1]

    return summary


def build_discussion_enrichment(
    summary: DebatDiscussionSummary,
    pack: DebatDiscussionInputPack,
    model_name: str,
) -> DebatDiscussionEnrichment:
    summary = attach_computed_refs(summary, pack)
    return DebatDiscussionEnrichment(
        **summary.model_dump(),
        dossier_uid=pack.dossier_uid,
        discussion_uid=pack.discussion_uid,
        debat_uid=pack.debat_uid,
        reunion_uid=pack.reunion_uid,
        point_odj_uid=pack.point_odj_uid,
        date_seance=pack.date_seance,
        intervenants_stats=pack.intervenants_stats,
        groupes_stats=pack.groupes_stats,
        source_refs=pack.source_refs,
        input_truncated=pack.input_truncated,
        model_name=model_name,
        agent_version=AGENT_VERSION,
        processed_at=datetime.now(tz=timezone.utc),
    )
