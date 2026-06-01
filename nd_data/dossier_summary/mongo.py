"""Mongo helpers shared by dossier summary scripts."""

import os

from pymongo import MongoClient
from pymongo.server_api import ServerApi


def get_mongo_collection(db_name: str, collection_name: str):
    mongo_pwd = os.environ["RC_MONGO_PWD"]
    mongo_username = os.environ["RC_MONGO_USERNAME"]
    mongo_host = os.environ["RC_MONGO_HOST"]
    uri = f"mongodb+srv://{mongo_username}:{mongo_pwd}@{mongo_host}/?retryWrites=true&w=majority&appName=RC"
    client = MongoClient(uri, server_api=ServerApi("1"))
    client.admin.command("ping")
    return client[db_name][collection_name]
