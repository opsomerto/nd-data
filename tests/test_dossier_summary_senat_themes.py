from nd_data.dossier_summary.themes import (
    find_senat_themes,
    senat_id_from_url,
    split_senat_theme_labels,
)
from nd_data.dossier_summary.themes import SenatThemeMatch


def test_senat_id_from_url():
    assert (
        senat_id_from_url("http://www.senat.fr/dossier-legislatif/pjl25-635.html")
        == "pjl25-635.html"
    )
    assert senat_id_from_url(None) is None


def test_split_senat_theme_labels():
    assert split_senat_theme_labels("Défense, Budget") == ["Défense", "Budget"]
    assert split_senat_theme_labels("") == []


def test_find_senat_themes_by_senat_chemin():
    match = SenatThemeMatch(
        senat_id="pjl25-635.html",
        source_url="http://www.senat.fr/dossier-legislatif/pjl25-635.html",
        labels_pretty=["Défense"],
        labels=["defense"],
    )

    found = find_senat_themes(
        "http://www.senat.fr/dossier-legislatif/pjl25-635.html",
        {"pjl25-635.html": match},
    )

    assert found == match
