from nd_data.dossier_summary.models import ActeurConcerne, Enjeu, StructuredSummary


def test_structured_summary_new_shape():
    summary = StructuredSummary(
        tldr="Le texte crée une obligation de transparence pour les plateformes numériques.",
        pourquoi="Le texte répond à un manque de visibilité sur les décisions de modération.",
        enjeux=[
            Enjeu(
                sujet="Transparence numérique",
                importance="Les utilisateurs doivent comprendre pourquoi des contenus sont retirés.",
                arbitrages="Équilibre entre libertés publiques et lutte contre les contenus illicites.",
            )
        ],
        ce_qui_change=[
            "Obliger les plateformes à publier un rapport annuel sur leurs procédures de modération.",
        ],
        acteurs_concernes=[
            ActeurConcerne(
                acteur="Plateformes numériques",
                impact="Elles doivent documenter et publier leurs pratiques de modération.",
            )
        ],
        objectif="Rendre les décisions de retrait de contenu plus compréhensibles et contrôlables.",
    )

    assert summary.enjeux[0].sujet == "Transparence numérique"
    assert summary.ce_qui_change[0].startswith("Obliger")
