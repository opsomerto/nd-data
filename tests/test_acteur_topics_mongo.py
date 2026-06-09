from nd_data.acteur_topics.mongo import topic_refs_from_dossier_doc


def test_topic_refs_from_dossier_doc_merges_senat_summary_and_keywords():
    doc = {
        "senat_theme_enrichment": {
            "labels": ["sante"],
            "labels_pretty": ["Santé"],
        },
        "dossier_summary_enrichment": {
            "qualification": {
                "themes_senat": ["sante", "justice"],
                "themes_ouverts": ["hôpital"],
                "keywords": ["urgences"],
            }
        },
    }

    topics = topic_refs_from_dossier_doc(doc)

    assert [(topic.namespace, topic.key, topic.label) for topic in topics] == [
        ("senat_theme", "sante", "Santé"),
        ("senat_theme", "justice", "justice"),
        ("open_theme", "hôpital", "hôpital"),
        ("keyword", "urgences", "urgences"),
    ]
