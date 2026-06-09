"""Mongo helpers for actor-topic enrichment."""

from datetime import UTC, datetime

from pymongo import DeleteMany, ReplaceOne, UpdateMany

from nd_data.acteur_topics.models import ActorDossierEvidence, ActorThemeProfile, TopicRef
from nd_data.dossier_summary.mongo import get_mongo_collection


def get_collection(db_name: str, collection_name: str):
    return get_mongo_collection(db_name, collection_name)


def ensure_evidence_indexes(collection) -> None:
    collection.create_index([("acteur_uid", 1), ("dossier_uid", 1)], unique=True)
    collection.create_index([("dossier_uid", 1)])
    collection.create_index([("acteur_uid", 1)])
    collection.create_index([("stale", 1), ("computed_at", 1)])


def ensure_profile_indexes(collection) -> None:
    collection.create_index([("acteur_uid", 1)], unique=True)
    collection.create_index([("main_senat_themes.key", 1)])
    collection.create_index([("main_open_themes.key", 1)])
    collection.create_index([("main_keywords.key", 1)])
    collection.create_index([("total_score", -1)])


def replace_dossier_evidence(
    collection,
    dossier_uid: str,
    docs: list[ActorDossierEvidence],
    delete_missing: bool = False,
) -> None:
    """Replace all evidence docs for a dossier while handling removed actor links."""
    operations = []
    now = datetime.now(UTC)
    fresh_actor_uids = [doc.acteur_uid for doc in docs]
    if delete_missing:
        operations.append(
            DeleteMany(
                {
                    "dossier_uid": dossier_uid,
                    "acteur_uid": {"$nin": fresh_actor_uids},
                }
            )
        )
    else:
        operations.append(
            UpdateMany(
                {
                    "dossier_uid": dossier_uid,
                    "acteur_uid": {"$nin": fresh_actor_uids},
                    "stale": {"$ne": True},
                },
                {"$set": {"stale": True, "stale_at": now}},
            )
        )
    operations.extend(
        ReplaceOne(
            {"acteur_uid": doc.acteur_uid, "dossier_uid": doc.dossier_uid},
            {"_id": doc.document_id, **doc.model_dump()},
            upsert=True,
        )
        for doc in docs
    )
    if operations:
        collection.bulk_write(operations, ordered=False)


def replace_actor_profiles(collection, profiles: list[ActorThemeProfile]) -> None:
    operations = [
        ReplaceOne(
            {"acteur_uid": profile.acteur_uid},
            {"_id": profile.acteur_uid, **profile.model_dump()},
            upsert=True,
        )
        for profile in profiles
    ]
    if operations:
        collection.bulk_write(operations, ordered=False)


def topic_refs_from_dossier_doc(doc: dict) -> list[TopicRef]:
    topics = []
    seen = set()

    def add(namespace: str, key: str | None, label: str | None = None) -> None:
        if not key:
            return
        item_key = (namespace, key)
        if item_key in seen:
            return
        seen.add(item_key)
        topics.append(TopicRef(namespace=namespace, key=key, label=label or key))

    senat = doc.get("senat_theme_enrichment") or {}
    for key, label in zip(
        senat.get("labels") or [], senat.get("labels_pretty") or [], strict=False
    ):
        add("senat_theme", key, label)

    qualification = (doc.get("dossier_summary_enrichment") or {}).get("qualification") or {}
    for key in qualification.get("themes_senat") or []:
        add("senat_theme", key)
    for key in qualification.get("themes_ouverts") or []:
        add("open_theme", key)
    for key in qualification.get("keywords") or []:
        add("keyword", key)
    return topics
