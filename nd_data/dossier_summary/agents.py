"""Pydantic AI agents for dossier summary modules."""

import json
import os
from datetime import datetime, timezone
from typing import Iterable

import dotenv
from pydantic_ai import Agent

from nd_data.dossier_summary.config import SETTINGS
from nd_data.dossier_summary.models import (
    DossierInputPack,
    DossierSummaryEnrichment,
    DossierSummaryOutput,
    NavetteSituation,
    Qualification,
    StructuredSummary,
    SummaryModule,
)
from nd_data.dossier_summary.themes import THEME_PRETTY_TO_LABEL

dotenv.load_dotenv()

AGENT_VERSION = "1.0"
DEFAULT_MODEL = SETTINGS.model


COMMON_RULES = """
Tu es un assistant expert en analyse législative française.
Réponds en français, pour un public non spécialiste.
Reste neutre, factuel et synthétique.
N'invente aucun chiffre, date, événement ou mesure.
Si une information manque, reste prudent et formule l'incertitude.
Évite le jargon; si un terme parlementaire est indispensable, rends-le compréhensible.
"""


QUALIFICATION_PROMPT = (
    COMMON_RULES
    + """
Mission: qualifier le dossier pour l'indexation documentaire.

Choisis jusqu'à 5 thèmes Sénat parmi cette table JSON {libelle_affiche: label_stocke}.
Retourne les labels stockés, pas les libellés affichés.

Exemple:
- Si le dossier concerne la police municipale, retourne "police_et_securite".
- Si le dossier concerne le logement social, retourne "logement_et_urbanisme".

Table des thèmes Sénat:
"""
    + json.dumps(THEME_PRETTY_TO_LABEL, ensure_ascii=False, indent=2)
    + """

Pour les thèmes ouverts et keywords:
- utilise des expressions courtes;
- préfère des thèmes concrets suivis dans l'actualité publique;
- évite les doublons et les termes trop génériques.
"""
)


STRUCTURED_SUMMARY_PROMPT = (
    COMMON_RULES
    + """
Mission: produire un résumé structuré et concret du contenu du dossier législatif.

Tu dois expliquer ce que le texte ferait concrètement s'il était adopté.

Consignes générales:
- Évite les formulations vagues ("renforcer", "améliorer", "favoriser") sans expliquer comment.
- Décris les changements juridiques ou opérationnels réels introduits par le texte.
- Mentionne explicitement les acteurs concernés: citoyens, entreprises, collectivités, administration, autorités publiques, secteurs professionnels, etc.
- Lorsque le texte modifie un dispositif existant, indique ce qui change par rapport à la situation actuelle.
- Ne mentionne pas la procédure parlementaire, les votes, les lectures ou la navette.
- Si les informations disponibles ne permettent pas d'être précis, reste sobre et ne complète pas avec des suppositions.

Format attendu:
- tldr: une phrase expliquant ce que ferait concrètement la loi si elle était adoptée.
- pourquoi: 2 à 5 phrases expliquant le problème à l'origine du texte, les constats ou critiques qui motivent son dépôt, et le contexte pertinent.
- enjeux: 2 à 5 enjeux majeurs si le texte s'y prête; 1 seul enjeu suffit pour un texte très ciblé. Pour chacun: sujet concerné, pourquoi c'est important, tensions ou arbitrages éventuels.
- ce_qui_change: 2 à 6 mesures concrètes si le texte en contient plusieurs; 1 seule mesure suffit pour un texte court ou très ciblé. Chaque mesure commence par un verbe d'action et décrit précisément le mécanisme prévu.
- acteurs_concernes: principaux acteurs touchés par le texte, avec l'impact concret pour chacun.
- objectif: 1 à 3 phrases expliquant le résultat concret recherché.

Pour "ce_qui_change", évite les intitulés abstraits. Décris des mécanismes.
Ne remplis pas artificiellement les listes: mieux vaut 2 mesures précises que 6 formulations vagues.

Bons exemples:
- "Obliger les plateformes à publier un rapport annuel sur leurs procédures de modération."
- "Créer une nouvelle procédure de contrôle exercée par l'autorité compétente."
- "Étendre l'éligibilité du dispositif aux entreprises de moins de 250 salariés."
- "Augmenter le plafond de l'aide financière de 5 000 à 10 000 euros."

Mauvais exemples:
- "Renforcer la transparence."
- "Améliorer le dispositif."
- "Favoriser l'accès aux droits."

Exemple de tldr:
"Le texte impose aux plateformes numériques de publier des indicateurs de modération et renforce les pouvoirs de contrôle de l'Arcom afin d'améliorer la transparence des décisions de retrait de contenu."

Cas particuliers:
- Pour une proposition de résolution, explique la position ou demande politique formulée, sans prétendre qu'elle crée directement des obligations juridiques.
- Pour un rapport ou une mission d'information, résume les constats et recommandations plutôt que des mesures législatives inexistantes.
- Pour un texte très court ou peu documenté, produis moins de mesures mais reste concret.
"""
)


NAVETTE_PROMPT = (
    COMMON_RULES
    + """
Mission: expliquer la situation actuelle du dossier dans la navette parlementaire.

Appuie-toi surtout sur navette_facts:
- statut et dernier acte;
- chronologie des actes;
- commissions saisies;
- débats en séance;
- amendements si disponibles;
- ancienneté et densité de la procédure.

Explique si le dossier:
- vient seulement d'être déposé;
- a commencé son examen en commission;
- a été débattu en séance;
- circule entre Assemblée et Sénat;
- est passé en CMP;
- est terminé, adopté, rejeté, retiré ou promulgué.

L'intensité doit être:
- "faible" pour peu d'actes, peu/pas de débat et procédure récente/simple;
- "moderee" pour une procédure engagée avec commission ou séance;
- "forte" pour nombreux débats, nombreux actes, CMP, lectures multiples ou fort volume d'amendements;
- "inconnue" si les faits sont insuffisants.

Ne transforme pas le résumé en commentaire politique. Explique où on en est et pourquoi c'est plus ou moins avancé.
"""
)


COMBINED_PROMPT = (
    COMMON_RULES
    + """
Mission: produire l'ensemble de l'enrichissement du dossier:
1. qualification documentaire;
2. résumé structuré du fond;
3. situation actuelle dans la navette.

Respecte les consignes des trois modules:
- thèmes Sénat: labels stockés parmi cette table;
- résumé structuré: contenu du texte, pas procédure;
- navette: état de la procédure, appuyé sur navette_facts.

Table des thèmes Sénat:
"""
    + json.dumps(THEME_PRETTY_TO_LABEL, ensure_ascii=False, indent=2)
)


qualification_agent: Agent[None, Qualification] = Agent(
    output_type=Qualification,
    system_prompt=QUALIFICATION_PROMPT.strip(),
    defer_model_check=True,
)

structured_summary_agent: Agent[None, StructuredSummary] = Agent(
    output_type=StructuredSummary,
    system_prompt=STRUCTURED_SUMMARY_PROMPT.strip(),
    defer_model_check=True,
)

navette_agent: Agent[None, NavetteSituation] = Agent(
    output_type=NavetteSituation,
    system_prompt=NAVETTE_PROMPT.strip(),
    defer_model_check=True,
)

combined_agent: Agent[None, DossierSummaryOutput] = Agent(
    output_type=DossierSummaryOutput,
    system_prompt=COMBINED_PROMPT.strip(),
    defer_model_check=True,
)


def pack_prompt(pack: DossierInputPack, module: SummaryModule | None = None) -> str:
    """Serialize only the fields each module needs to keep prompts compact."""
    payload = pack.model_dump(mode="json", exclude_none=True)
    if module is not None and module != SummaryModule.navette_situation:
        payload.pop("navette_facts", None)
    if module == SummaryModule.navette_situation:
        for document in payload.get("source_documents", []):
            document.pop("text", None)
    return (
        "Voici le dossier à analyser, au format JSON compact. "
        "Utilise uniquement ces informations.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def run_qualification(pack: DossierInputPack, model_name: str = DEFAULT_MODEL) -> Qualification:
    result = qualification_agent.run_sync(
        pack_prompt(pack, SummaryModule.qualification),
        model=model_name,
    )
    return result.output


def run_structured_summary(
    pack: DossierInputPack,
    model_name: str = DEFAULT_MODEL,
) -> StructuredSummary:
    result = structured_summary_agent.run_sync(
        pack_prompt(pack, SummaryModule.structured_summary),
        model=model_name,
    )
    return result.output


def run_navette_situation(
    pack: DossierInputPack,
    model_name: str = DEFAULT_MODEL,
) -> NavetteSituation:
    result = navette_agent.run_sync(
        pack_prompt(pack, SummaryModule.navette_situation),
        model=model_name,
    )
    return result.output


def run_combined(pack: DossierInputPack, model_name: str = DEFAULT_MODEL) -> DossierSummaryOutput:
    result = combined_agent.run_sync(pack_prompt(pack), model=model_name)
    return result.output


def build_enrichment(
    output: DossierSummaryOutput,
    pack: DossierInputPack,
    modules: Iterable[SummaryModule],
    source_options: Iterable[str],
    model_name: str,
) -> DossierSummaryEnrichment:
    return DossierSummaryEnrichment(
        **output.model_dump(),
        model_name=model_name,
        agent_version=AGENT_VERSION,
        modules_processed=list(modules),
        source_options=list(source_options),
        source_document_refs=[source.uid for source in pack.source_documents],
        processed_at=datetime.now(tz=timezone.utc),
    )
