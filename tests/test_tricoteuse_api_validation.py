import httpx

from nd_data.tricoteuse_api import TricoteuseAPIClient


def test_get_amendements_normalizes_programme_lists():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "uid": "AMD1",
                        "listeProgrammesPLFR": [{"action": "modification"}],
                        "listeProgrammesPLF": [{"action": "modification"}],
                    }
                ]
            },
        )

    client = TricoteuseAPIClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    amendements = client.get_amendements(dossierRefUid="D1")

    assert len(amendements) == 1
    assert amendements[0].uid == "AMD1"
    assert amendements[0].listeProgrammesPLFR is None
    assert amendements[0].listeProgrammesPLF is None
