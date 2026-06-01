"""Deterministic compression of legislative acts for navette situation prompts."""

from collections import Counter
from datetime import timedelta

from nd_data.dossier_summary.models import (
    NavetteActivityMetrics,
    NavetteFacts,
    NavetteTimelineItem,
)
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import ActeLegislatif, Dossier


SEANCE_CODE_PARTS = ("DEBATS", "SEANCE")
COMMISSION_CODE_PARTS = ("COM-", "COM_")
READING_MARKERS = ("AN1", "SN1", "AN2", "SN2", "AN3", "SN3", "ANNLEC", "CMP")


def enum_value(value):
    return getattr(value, "value", value)


def is_seance_debate(acte: ActeLegislatif) -> bool:
    code = acte.codeActe or ""
    return "DEBATS" in code and ("SEANCE" in code or code.startswith("SN"))


def is_commission_acte(acte: ActeLegislatif) -> bool:
    code = acte.codeActe or ""
    return "COM-" in code or "-COM" in code or "COMMISSION" in (acte.etape or "").upper()


def reading_marker(code: str | None) -> str | None:
    if not code:
        return None
    for marker in READING_MARKERS:
        if code.startswith(marker):
            return marker
    return None


def acte_label(acte: ActeLegislatif) -> str:
    return (
        acte.libelleCourtActe
        or acte.nomCanonique
        or acte.etape
        or acte.codeActe
        or "Acte législatif"
    )


def duration_minutes(start, end) -> int | None:
    if not start or not end:
        return None
    delta = end - start
    if not isinstance(delta, timedelta):
        return None
    minutes = int(delta.total_seconds() // 60)
    return max(minutes, 0)


def resolve_commissions(
    actes: list[ActeLegislatif],
    client: TricoteuseAPIClient | None,
) -> list[str]:
    """Return readable commission names, falling back to organe UIDs when lookup fails."""
    labels: list[str] = []
    seen: set[str] = set()
    for acte in actes:
        if not is_commission_acte(acte) or not acte.organeRefUid or acte.organeRefUid in seen:
            continue
        seen.add(acte.organeRefUid)
        label = acte.organeRefUid
        if acte.organeRef:
            label = acte.organeRef.libelle or acte.organeRef.libelleAbrege or label
        elif client is not None:
            try:
                organe = client.get_organe(acte.organeRefUid)
                if organe:
                    label = organe.libelle or organe.libelleAbrege or label
            except Exception:
                label = acte.organeRefUid
        labels.append(label)
    return labels


def estimate_debate_duration_minutes(
    actes: list[ActeLegislatif],
    client: TricoteuseAPIClient | None,
) -> int | None:
    """Sum public-session reunion durations referenced by debate actes.

    This is a useful proxy, not a perfect speaking-time metric: a reunion can contain
    several agenda points, and Tricoteuse does not always expose per-dossier timing.
    """
    if client is None:
        return None
    total = 0
    found = False
    seen_reunions: set[str] = set()
    for acte in actes:
        if (
            not is_seance_debate(acte)
            or not acte.reunionRefUid
            or acte.reunionRefUid in seen_reunions
        ):
            continue
        seen_reunions.add(acte.reunionRefUid)
        try:
            reunion = client.get_reunion(acte.reunionRefUid)
        except Exception:
            continue
        if not reunion:
            continue
        minutes = duration_minutes(reunion.timestampDebut, reunion.timestampFin)
        if minutes is not None:
            total += minutes
            found = True
    return total if found else None


def get_amendment_count(
    dossier: Dossier,
    client: TricoteuseAPIClient | None = None,
) -> int | None:
    """Return amendment count from included data or Tricoteuse computed count."""
    if dossier.amendements is not None:
        return len(dossier.amendements)
    if client is None or not dossier.uid:
        return None
    try:
        response = client.get_dossier(dossier.uid, computed_fields=["_count.amendements"])
    except Exception:
        return None
    if response is None:
        return None
    return response.count("amendements")


def build_navette_facts(
    dossier: Dossier,
    client: TricoteuseAPIClient | None = None,
    fetch_debate_durations: bool = False,
    fetch_amendment_count: bool = False,
) -> NavetteFacts:
    """Compress verbose legislative acts into facts the navette agent can reason from."""
    actes = sorted(
        dossier.actesLegislatifs or [],
        key=lambda acte: (acte.dateActe is None, acte.dateActe, acte.uid or ""),
    )
    seance_debates = [acte for acte in actes if is_seance_debate(acte)]
    commission_actes = [acte for acte in actes if is_commission_acte(acte)]
    reading_markers = {reading_marker(acte.codeActe) for acte in actes}
    reading_markers.discard(None)

    commissions = resolve_commissions(commission_actes, client)
    debate_minutes = (
        estimate_debate_duration_minutes(actes, client) if fetch_debate_durations else None
    )
    amendment_count = get_amendment_count(dossier, client) if fetch_amendment_count else None
    acte_uids = [acte.uid for acte in actes if acte.uid]
    counter = Counter(enum_value(acte.chambre) for acte in actes if acte.chambre)
    current_chamber = counter.most_common(1)[0][0] if counter else enum_value(dossier.chambre)

    key_actes = select_timeline_actes(actes, dossier.uidDernierActe)
    timeline = [
        NavetteTimelineItem(
            date=acte.dateActe,
            chambre=enum_value(acte.chambre),
            label=acte_label(acte),
            acte_uid=acte.uid,
            acte_code=acte.codeActe,
        )
        for acte in key_actes
    ]

    last_acte = next((acte for acte in reversed(actes) if acte.uid == dossier.uidDernierActe), None)
    if last_acte is None and actes:
        last_acte = actes[-1]

    notes = []
    if amendment_count is None:
        notes.append("Nombre d'amendements non collecté dans ce pack.")
    if debate_minutes is None:
        notes.append("Durée des débats non collectée dans ce pack.")

    return NavetteFacts(
        current_status=dossier.statut,
        current_state=dossier.etat,
        current_chamber=current_chamber,
        last_acte_uid=dossier.uidDernierActe or (last_acte.uid if last_acte else None),
        last_acte_code=dossier.codeDernierActe or (last_acte.codeActe if last_acte else None),
        last_acte_label=acte_label(last_acte) if last_acte else None,
        metrics=NavetteActivityMetrics(
            nb_actes=len(actes),
            nb_commissions=len(commissions),
            commissions=commissions,
            nb_debats_seance=len(seance_debates),
            temps_debat_seance_minutes=debate_minutes,
            nb_amendements=amendment_count,
            nb_documents=len(dossier.documents or []),
            nb_lectures=len(reading_markers),
            has_cmp=any("CMP" in (acte.codeActe or "") for acte in actes),
            has_promulgation=any(
                "PROM" in (acte.codeActe or "") or acte.urlLegifrance for acte in actes
            ),
        ),
        timeline=timeline,
        evidence_acte_uids=acte_uids[-12:],
        notes=notes,
    )


def select_timeline_actes(
    actes: list[ActeLegislatif],
    last_acte_uid: str | None,
    max_items: int = 8,
) -> list[ActeLegislatif]:
    """Keep first, last and procedurally important acts so the LLM gets a short chronology."""
    if len(actes) <= max_items:
        return actes
    selected: list[ActeLegislatif] = []
    selected.extend(actes[:2])
    selected.extend(
        acte
        for acte in actes
        if acte.uid == last_acte_uid
        or is_seance_debate(acte)
        or acte.adoption is not None
        or "CMP" in (acte.codeActe or "")
        or "PROM" in (acte.codeActe or "")
    )
    selected.extend(actes[-2:])

    deduped: list[ActeLegislatif] = []
    seen: set[str] = set()
    for acte in selected:
        key = acte.uid or f"{acte.codeActe}-{acte.dateActe}"
        if key not in seen:
            deduped.append(acte)
            seen.add(key)
    if len(deduped) > max_items:
        return deduped[: max_items - 1] + [deduped[-1]]
    return deduped
