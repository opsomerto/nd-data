from nd_data.tricoteuse_models import Dossier
from scripts.enrich_dossier_summary import count_dossiers, iter_dossiers


class FakeTricoteuseClient:
    def __init__(self):
        self.pages_requested: list[int] = []

    def get_dossier(self, uid, include=None):
        return Dossier(uid=uid)

    def get_dossiers(self, page, per_page, chambre, sort, include):
        self.pages_requested.append(page)
        pages = {
            1: [Dossier(uid="D1"), Dossier(uid="D2")],
            2: [Dossier(uid="D3"), Dossier(uid="D4")],
            3: [Dossier(uid="D5")],
        }
        return pages.get(page, [])

    def get_dossiers_total(self, chambre=None):
        return 5


def test_iter_dossiers_streams_pages_until_limit():
    client = FakeTricoteuseClient()

    dossiers = list(iter_dossiers(client, uid=None, chambre="AN", per_page=2, limit=3))

    assert [dossier.uid for dossier in dossiers] == ["D1", "D2", "D3"]
    assert client.pages_requested == [1, 2]


def test_count_dossiers_uses_api_total_with_limit():
    client = FakeTricoteuseClient()

    assert count_dossiers(client, uid=None, chambre="AN", limit=3) == 3
    assert count_dossiers(client, uid=None, chambre="AN", limit=None) == 5
    assert count_dossiers(client, uid="D1", chambre="AN", limit=None) == 1
