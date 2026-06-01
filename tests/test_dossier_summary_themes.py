from nd_data.dossier_summary.themes import (
    THEME_LABEL_TO_PRETTY,
    normalize_senat_theme,
    pretty_senat_theme,
    slugify_theme,
)


def test_slugify_theme_removes_accents_and_punctuation():
    assert slugify_theme("Police et sécurité") == "police_et_securite"
    assert slugify_theme("Économie et finances") == "economie_et_finances"
    assert slugify_theme("Outre-mer") == "outre_mer"


def test_normalize_senat_theme_accepts_pretty_and_label():
    assert normalize_senat_theme("Police et sécurité") == "police_et_securite"
    assert normalize_senat_theme("police_et_securite") == "police_et_securite"


def test_pretty_senat_theme_roundtrip():
    assert THEME_LABEL_TO_PRETTY["police_et_securite"] == "Police et sécurité"
    assert pretty_senat_theme("police_et_securite") == "Police et sécurité"
