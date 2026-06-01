"""Build compact, typed input packs for dossier summary agents."""

import hashlib
import re
from pathlib import Path
from tempfile import gettempdir
from typing import Iterable

import httpx

from nd_data.dossier_summary.config import SETTINGS
from nd_data.dossier_summary.models import (
    DossierInputPack,
    SourceDocument,
    SourceKind,
)
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import ActeLegislatif, Document, Dossier

DEFAULT_SOURCE_OPTIONS = ("initial",)
PDF_CACHE_DIR = SETTINGS.pdf_cache_dir
MAX_SOURCE_CHARS = SETTINGS.max_source_chars


ANNEX_HINTS = (
    "avis du conseil d'etat",
    "conseil d'etat",
    "etude d'impact",
    "étude d'impact",
)
REPORT_HINTS = ("rapport", "avis")
ADOPTED_HINTS = ("texte adopté", "texte adopte")


def clean_text(value: str | None) -> str | None:
    """Collapse HTML-ish content and whitespace into plain text for LLM input."""
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def document_search_text(document: Document, acte_code: str | None = None) -> str:
    """Collect document metadata used to infer whether it is an annex or report."""
    fields = [
        document.titrePrincipal,
        document.titrePrincipalCourt,
        document.classeLibelle,
        document.depotLibelle,
        document.especeLibelle,
        document.typeLibelle,
        document.sousTypeLibelle,
        document.sousTypeLibelleEdition,
        acte_code,
    ]
    return " ".join(str(field) for field in fields if field).lower()


def classify_document(
    document: Document,
    dossier: Dossier,
    acte_code: str | None = None,
) -> SourceKind:
    """Classify a Tricoteuse document into the source packs exposed to the CLI."""
    if document.uid and document.uid == dossier.documentDeposeRefUid:
        return SourceKind.initial

    text = document_search_text(document, acte_code)
    text_ascii = text.encode("ascii", "ignore").decode("ascii")

    if any(hint in text or hint in text_ascii for hint in ANNEX_HINTS):
        return SourceKind.annex
    if any(hint in text or hint in text_ascii for hint in ADOPTED_HINTS):
        return SourceKind.adopted_text
    if any(hint in text or hint in text_ascii for hint in REPORT_HINTS):
        return SourceKind.report
    if acte_code and ("RAPPORT" in acte_code or "AVIS" in acte_code):
        return SourceKind.report
    return SourceKind.other


def cache_path_for_url(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return PDF_CACHE_DIR / f"{digest}.md"


def extract_pdf_markdown(pdf_url: str, timeout: float = 30.0) -> str | None:
    """Extract Markdown from a PDF URL with PyMuPDF4LLM and cache the result."""
    cache_path = cache_path_for_url(pdf_url)
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    try:
        import pymupdf4llm
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf4llm is required for PDF extraction. Install dependencies with uv sync."
        ) from exc

    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(pdf_url)
        response.raise_for_status()
    pdf_path = cache_path.with_suffix(".pdf")
    pdf_path.write_bytes(response.content)
    markdown = pymupdf4llm.to_markdown(str(pdf_path))
    cache_path.write_text(markdown or "", encoding="utf-8")
    try:
        pdf_path.unlink()
    except OSError:
        pass
    return markdown or None


def source_document_from_document(
    document: Document,
    kind: SourceKind,
    acte_code: str | None = None,
    include_pdf_text: bool = False,
    max_chars: int | None = None,
) -> SourceDocument | None:
    """Build the compact source-document payload, using PDF text only as fallback."""
    max_chars = max_chars or SETTINGS.max_source_chars
    if not document.uid:
        return None
    text = clean_text(document.exposeMotifsTexte)
    text_origin = "model" if text else "none"

    if include_pdf_text and not text and document.pdfUrl:
        extracted = extract_pdf_markdown(document.pdfUrl)
        text = clean_text(extracted)
        text_origin = "pdf" if text else "none"

    original_text_chars = len(text) if text else None
    text_truncated = bool(text and len(text) > max_chars)
    if text_truncated:
        text = text[:max_chars].rsplit(" ", 1)[0] + " [...]"
    text_chars = len(text) if text else None

    return SourceDocument(
        uid=document.uid,
        kind=kind,
        title=document.titrePrincipal or document.titrePrincipalCourt,
        type_code=document.typeCode,
        type_label=document.typeLibelle,
        sous_type_code=document.sousTypeCode,
        sous_type_label=document.sousTypeLibelle,
        acte_code=acte_code,
        pdf_url=document.pdfUrl,
        text=text,
        text_origin=text_origin,
        original_text_chars=original_text_chars,
        text_chars=text_chars,
        text_truncated=text_truncated,
    )


def documents_by_uid(dossier: Dossier) -> dict[str, Document]:
    return {document.uid: document for document in dossier.documents or [] if document.uid}


def acte_document_links(actes: Iterable[ActeLegislatif]) -> dict[str, str | None]:
    """Map documents referenced by legislative acts to the first acte code that mentions them."""
    links: dict[str, str | None] = {}
    for acte in actes:
        for uid in (acte.documentRefUid, acte.texteAssocieRefUid, acte.texteAdopteRefUid):
            if uid and uid not in links:
                links[uid] = acte.codeActe
    return links


def collect_source_documents(
    dossier: Dossier,
    client: TricoteuseAPIClient | None = None,
    source_options: Iterable[str] = DEFAULT_SOURCE_OPTIONS,
    include_pdf_text: bool = False,
    max_source_chars: int | None = None,
) -> list[SourceDocument]:
    """Fetch and classify the documents requested by source_options."""
    options = set(source_options)
    client = client or TricoteuseAPIClient()
    documents = documents_by_uid(dossier)
    acte_links = acte_document_links(dossier.actesLegislatifs or [])
    collected: list[SourceDocument] = []
    seen: set[str] = set()

    candidate_uids: list[tuple[str, str | None]] = []
    if dossier.documentDeposeRefUid:
        candidate_uids.append((dossier.documentDeposeRefUid, None))
    candidate_uids.extend(acte_links.items())
    candidate_uids.extend((uid, None) for uid in documents)

    for uid, acte_code in candidate_uids:
        if not uid or uid in seen:
            continue
        document = documents.get(uid)
        if document is None:
            fetched = client.get_document(uid)
            if fetched is None:
                continue
            document = fetched
        kind = classify_document(document, dossier, acte_code=acte_code)
        if kind.value not in options and not (kind == SourceKind.initial and "initial" in options):
            continue
        source = source_document_from_document(
            document,
            kind=kind,
            acte_code=acte_code,
            include_pdf_text=include_pdf_text,
            max_chars=max_source_chars,
        )
        if source:
            collected.append(source)
            seen.add(uid)

    return collected


def build_dossier_input_pack(
    dossier: Dossier,
    client: TricoteuseAPIClient | None = None,
    source_options: Iterable[str] = DEFAULT_SOURCE_OPTIONS,
    include_pdf_text: bool = False,
    fetch_debate_durations: bool = False,
    fetch_amendment_count: bool = False,
    max_source_chars: int | None = None,
) -> DossierInputPack:
    """Build the complete LLM input pack from dossier metadata, documents and navette facts."""
    from nd_data.dossier_summary.navette import build_navette_facts

    client = client or TricoteuseAPIClient()
    source_documents = collect_source_documents(
        dossier,
        client=client,
        source_options=source_options,
        include_pdf_text=include_pdf_text,
        max_source_chars=max_source_chars,
    )
    return DossierInputPack(
        uid=dossier.uid or "",
        titre=dossier.titre,
        chambre=getattr(dossier.chambre, "value", dossier.chambre),
        legislature=dossier.legislature,
        type_initiative=dossier.typeInitiative,
        type_procedure=dossier.typeProcedure,
        code_procedure=dossier.codeProcedure,
        statut=dossier.statut,
        etat=dossier.etat,
        date_depot=dossier.dateDepot,
        date_dernier_acte=dossier.dateDernierActe,
        code_dernier_acte=dossier.codeDernierActe,
        uid_dernier_acte=dossier.uidDernierActe,
        procedure_acceleree=dossier.procedureAcceleree,
        retrait_initiative=dossier.retraitInitiative,
        source_documents=source_documents,
        navette_facts=build_navette_facts(
            dossier,
            client=client,
            fetch_debate_durations=fetch_debate_durations,
            fetch_amendment_count=fetch_amendment_count,
        ),
    )


def test():
    client = TricoteuseAPIClient()
    # dossiers = client.get_dossiers(
    #     per_page=10, include=["actesLegislatifs", "documents"], chambre="AN"
    # )
    # dossier = dossiers[8]
    dossier = client.get_dossier("DLR5L17N54085", include=["actesLegislatifs", "documents"])
    input_pack = build_dossier_input_pack(
        dossier,
        client=client,
        source_options=["initial", "annex", "report", "adopted_text"],
        include_pdf_text=False,
        fetch_debate_durations=True,
    )
    print(input_pack.model_dump_json(indent=2))

    dossier.documentDeposeRefUid
    doc = client.get_document(dossier.documentDeposeRefUid)
    print(doc.model_dump_json(indent=2))
