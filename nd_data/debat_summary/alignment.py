"""Align real compte-rendu sections with legislative dossiers."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import unescape
import re
import unicodedata

from nd_data.debat_summary.models import (
    AlignmentCandidateScore,
    DebatAlignmentDocument,
    DebatSectionAlignment,
)
from nd_data.debat_summary.sources import (
    code_grammaire,
    is_interruption,
    is_speech_intervention,
    sort_interventions,
    value,
)
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import Agenda, Debat, Dossier, Paragraphe, PointOdj


ALIGNMENT_VERSION = "debat_section_alignment_v1"
TITLE_CODES = (
    "TITRE_TEXTE_DISCUSSION",
    "SOUS_TITRE_TEXTE_DISCUSSION",
    "PRESENTATION",
    "DISC_GENERALE",
    "DISC_ARTICLES",
    "VOTE_ENS",
)
EXCLUDED_SECTION_CODE_PREFIXES = ("OUV_SEAN", "FIN_SEAN")
EXCLUDED_SECTION_TITLE_PATTERNS = (
    "ouverture de la seance",
    "ordre du jour de la prochaine seance",
    "ordre du jour des prochaines seances",
    "fin de la seance",
    "cloture de la seance",
)
GENERIC_SECTION_TOKENS = {
    "discussion",
    "generale",
    "article",
    "articles",
    "unique",
    "amendement",
    "amendements",
    "scrutin",
    "vote",
    "votes",
    "explication",
    "explications",
}
STOPWORDS = {
    "de",
    "du",
    "des",
    "la",
    "le",
    "les",
    "un",
    "une",
    "et",
    "a",
    "au",
    "aux",
    "en",
    "pour",
    "par",
    "sur",
    "dans",
    "d",
    "l",
    "proposition",
    "projet",
    "loi",
    "discussion",
    "suite",
    "ordre",
    "jour",
    "seance",
    "seances",
    "prochaine",
    "prochaines",
    "article",
    "articles",
    "amendement",
    "amendements",
    "generale",
    "unique",
}


@dataclass
class DossierCandidate:
    dossier_uid: str
    title: str | None = None
    point: PointOdj | None = None


@dataclass
class SectionDescriptor:
    real_ordre_point: str
    paragraphs: list[Paragraphe]
    title: str | None
    section_type: str | None
    match_text: str
    metadata_dossier_uids: set[str]
    metadata_point_uids: set[str]


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def tokens(text: str | None) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", clean_text(text))
        if len(token) > 2 and token not in STOPWORDS
    }


def token_matches(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) < 5 or len(right) < 5:
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.88


def common_tokens(source_tokens: set[str], candidate_tokens: set[str]) -> list[str]:
    common = []
    for candidate_token in sorted(candidate_tokens):
        if any(token_matches(candidate_token, source_token) for source_token in source_tokens):
            common.append(candidate_token)
    return common


def specific_title_tokens(title: str | None) -> set[str]:
    return tokens(title) - GENERIC_SECTION_TOKENS


def paragraph_text(paragraph: Paragraphe) -> str:
    return paragraph.texte or paragraph.sommaire or paragraph.structure or paragraph.valeur or ""


def section_sort_key(value_: str) -> tuple[bool, int, str]:
    try:
        return (False, int(value_), value_)
    except ValueError:
        return (True, 0, value_)


def grouped_sections(paragraphs: list[Paragraphe]) -> list[tuple[str, list[Paragraphe]]]:
    groups: dict[str, list[Paragraphe]] = defaultdict(list)
    for paragraph in paragraphs:
        groups[paragraph.valeurPtsOdj or "unknown"].append(paragraph)
    return [
        (key, sort_interventions(items))
        for key, items in sorted(groups.items(), key=lambda item: section_sort_key(item[0]))
    ]


def is_section_context(paragraph: Paragraphe) -> bool:
    return not is_speech_intervention(paragraph) and not is_interruption(paragraph)


def is_excluded_section(section: SectionDescriptor) -> bool:
    code = section.section_type or ""
    if code.startswith(EXCLUDED_SECTION_CODE_PREFIXES):
        return True
    clean_title = clean_text(section.title)
    return any(pattern in clean_title for pattern in EXCLUDED_SECTION_TITLE_PATTERNS)


def build_section_descriptor(
    real_ordre_point: str,
    paragraphs: list[Paragraphe],
) -> SectionDescriptor:
    context_paragraphs = [paragraph for paragraph in paragraphs if is_section_context(paragraph)]
    non_interruption_paragraphs = [
        paragraph for paragraph in paragraphs if not is_interruption(paragraph)
    ]
    title_candidates = [
        paragraph_text(paragraph)
        for paragraph in context_paragraphs
        if (code_grammaire(paragraph) or "").startswith(TITLE_CODES)
    ]
    if not title_candidates:
        title_candidates = [paragraph_text(paragraph) for paragraph in context_paragraphs[:3]]
    title = next((item for item in title_candidates if item), None)
    section_type = next(
        (
            code_grammaire(paragraph)
            for paragraph in context_paragraphs
            if code_grammaire(paragraph)
        ),
        None,
    )
    match_chunks = title_candidates + [
        paragraph_text(paragraph) for paragraph in context_paragraphs[:8]
    ]
    if len(tokens(" ".join(match_chunks))) < 3:
        match_chunks.extend(
            paragraph_text(paragraph) for paragraph in non_interruption_paragraphs[:5]
        )
    return SectionDescriptor(
        real_ordre_point=real_ordre_point,
        paragraphs=paragraphs,
        title=title,
        section_type=section_type,
        match_text=" ".join(chunk for chunk in match_chunks if chunk),
        metadata_dossier_uids={
            paragraph.dossierRefUid for paragraph in paragraphs if paragraph.dossierRefUid
        },
        metadata_point_uids={
            paragraph.pointOdjRefUid for paragraph in paragraphs if paragraph.pointOdjRefUid
        },
    )


def candidate_text(candidate: DossierCandidate) -> str:
    parts = [candidate.title]
    if candidate.point:
        parts.extend([candidate.point.objet, candidate.point.typePointOdj])
    return " ".join(part for part in parts if part)


def score_candidate(
    section: SectionDescriptor, candidate: DossierCandidate
) -> AlignmentCandidateScore:
    candidate_title = candidate.title or candidate_text(candidate)
    candidate_title_clean = clean_text(candidate_title)
    candidate_title_tokens = tokens(candidate_title)
    title_tokens = specific_title_tokens(section.title)

    if section.title and title_tokens and candidate_title_clean:
        common = common_tokens(title_tokens, candidate_title_tokens)
        title_clean = clean_text(section.title)
        sequence_score = SequenceMatcher(None, title_clean, candidate_title_clean).ratio()
        candidate_overlap = len(common) / max(1, len(candidate_title_tokens))
        section_overlap = len(common) / max(1, len(title_tokens))
        score = 0.65 * candidate_overlap + 0.25 * section_overlap + 0.10 * sequence_score
        if common:
            if candidate.dossier_uid in section.metadata_dossier_uids:
                score += 0.02
            if candidate.point and candidate.point.uid in section.metadata_point_uids:
                score += 0.01
        else:
            score = min(score, 0.2)
        return AlignmentCandidateScore(
            dossier_uid=candidate.dossier_uid,
            title=candidate.title,
            score=round(min(score, 1.0), 3),
            evidence=common[:8],
        )

    section_clean = clean_text(section.match_text)
    candidate_clean = clean_text(candidate_text(candidate))
    if not section_clean or not candidate_clean:
        return AlignmentCandidateScore(
            dossier_uid=candidate.dossier_uid,
            title=candidate.title,
            score=0.0,
        )
    sequence_score = SequenceMatcher(None, section_clean, candidate_clean).ratio()
    section_tokens = tokens(section.match_text)
    candidate_tokens = tokens(candidate_text(candidate))
    common = common_tokens(section_tokens, candidate_tokens)
    overlap_score = len(common) / max(1, len(candidate_tokens))
    score = 0.35 * sequence_score + 0.65 * overlap_score
    if common and candidate.dossier_uid in section.metadata_dossier_uids:
        score += 0.02
    if common and candidate.point and candidate.point.uid in section.metadata_point_uids:
        score += 0.01
    return AlignmentCandidateScore(
        dossier_uid=candidate.dossier_uid,
        title=candidate.title,
        score=round(min(score, 1.0), 3),
        evidence=common[:8],
    )


def confidence_for(score: float, margin: float) -> str:
    if score < 0.25:
        return "unresolved"
    if score >= 0.55 and margin >= 0.08:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def align_section(
    section: SectionDescriptor,
    candidates: list[DossierCandidate],
) -> DebatSectionAlignment:
    if is_excluded_section(section):
        return DebatSectionAlignment(
            real_ordre_point=section.real_ordre_point,
            section_title=section.title,
            section_type=section.section_type,
            paragraph_dossier_uids=sorted(section.metadata_dossier_uids),
            paragraph_point_odj_uids=sorted(section.metadata_point_uids),
            warnings=["procedural_section_ignored"],
        )

    scores = sorted(
        [score_candidate(section, candidate) for candidate in candidates],
        key=lambda item: (-item.score, item.dossier_uid),
    )
    best = scores[0] if scores else None
    second = scores[1] if len(scores) > 1 else None
    margin = (best.score - second.score) if best and second else (best.score if best else 0.0)
    confidence = confidence_for(best.score, margin) if best else "unresolved"
    matched_uid = best.dossier_uid if best and confidence != "unresolved" else None
    planned_point = next(
        (candidate.point for candidate in candidates if candidate.dossier_uid == matched_uid),
        None,
    )
    warnings = []
    if second and margin < 0.08:
        warnings.append("candidate_scores_close")
    if (
        matched_uid
        and section.metadata_dossier_uids
        and matched_uid not in section.metadata_dossier_uids
    ):
        warnings.append("paragraph_metadata_points_to_other_dossier")
    if (
        planned_point
        and planned_point.ordrePoint is not None
        and str(planned_point.ordrePoint) != section.real_ordre_point
    ):
        warnings.append("planned_ordre_point_differs_from_real_section")
    return DebatSectionAlignment(
        real_ordre_point=section.real_ordre_point,
        section_title=section.title,
        section_type=section.section_type,
        matched_dossier_uid=matched_uid,
        confidence=confidence,
        score=best.score if best else 0.0,
        paragraph_dossier_uids=sorted(section.metadata_dossier_uids),
        paragraph_point_odj_uids=sorted(section.metadata_point_uids),
        planned_point_odj_uid=planned_point.uid if planned_point else None,
        planned_ordre_point=planned_point.ordrePoint if planned_point else None,
        evidence=(best.evidence if best else []),
        candidate_scores=scores[:5],
        warnings=warnings,
    )


def fetch_candidate_dossier(
    client: TricoteuseAPIClient,
    dossier_uid: str,
) -> Dossier | None:
    try:
        return client.get_dossier(dossier_uid)
    except Exception:
        return None


def build_candidates(
    client: TricoteuseAPIClient,
    reunion: Agenda,
    seed_dossiers: list[Dossier] | None = None,
) -> list[DossierCandidate]:
    seed_by_uid = {dossier.uid: dossier for dossier in seed_dossiers or [] if dossier.uid}
    point_by_dossier = {}
    for point in reunion.pointsOdj or []:
        if point.dossierLegislatifUid and point.dossierLegislatifUid not in point_by_dossier:
            point_by_dossier[point.dossierLegislatifUid] = point
    candidate_uids = sorted(set(point_by_dossier) | set(seed_by_uid))
    candidates = []
    for dossier_uid in candidate_uids:
        dossier = seed_by_uid.get(dossier_uid) or fetch_candidate_dossier(client, dossier_uid)
        point = point_by_dossier.get(dossier_uid)
        title = dossier.titre if dossier else None
        if not title and point:
            title = point.objet
        candidates.append(DossierCandidate(dossier_uid=dossier_uid, title=title, point=point))
    return candidates


def build_alignment_document(
    client: TricoteuseAPIClient,
    reunion: Agenda,
    debat: Debat,
    seed_dossiers: list[Dossier] | None = None,
    computed_at: datetime | None = None,
) -> DebatAlignmentDocument:
    candidates = build_candidates(client, reunion, seed_dossiers=seed_dossiers)
    sections = [
        align_section(build_section_descriptor(real_ordre_point, paragraphs), candidates)
        for real_ordre_point, paragraphs in grouped_sections(debat.paragraphes or [])
    ]
    return DebatAlignmentDocument(
        debat_uid=debat.uid or reunion.compteRenduRefUid or "",
        reunion_uid=reunion.uid,
        date_seance=reunion.dateSeance,
        chambre=value(reunion, "chambre"),
        sections=sections,
        algorithm_version=ALIGNMENT_VERSION,
        computed_at=computed_at or datetime.now(tz=timezone.utc),
    )
