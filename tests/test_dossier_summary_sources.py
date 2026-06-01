from nd_data.dossier_summary.models import SourceKind
from nd_data.dossier_summary.sources import classify_document, source_document_from_document
from nd_data.tricoteuse_models import Document, Dossier


def test_classify_initial_document():
    dossier = Dossier(uid="D1", documentDeposeRefUid="DOC1")
    document = Document(uid="DOC1", titrePrincipal="Projet de loi test")

    assert classify_document(document, dossier) == SourceKind.initial


def test_classify_impact_study_as_annex():
    dossier = Dossier(uid="D1", documentDeposeRefUid="DOC1")
    document = Document(uid="DOC2", titrePrincipal="Etude d'impact")

    assert classify_document(document, dossier) == SourceKind.annex


def test_classify_commission_report():
    dossier = Dossier(uid="D1", documentDeposeRefUid="DOC1")
    document = Document(uid="DOC2", titrePrincipal="Rapport de la commission")

    assert classify_document(document, dossier) == SourceKind.report


def test_source_document_records_truncation_metadata():
    document = Document(uid="DOC1", titrePrincipal="Projet", exposeMotifsTexte="a " * 20)

    source = source_document_from_document(document, SourceKind.initial, max_chars=10)

    assert source.text_truncated is True
    assert source.original_text_chars == 39
    assert source.text_chars <= 16
