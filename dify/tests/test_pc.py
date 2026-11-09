import httpx
from backend.pc import PowerContext


def test_uncertain_write_reconciliation_matches_server_unicode_normalization():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.url.path == "/v1/memory/entries/list"
        return httpx.Response(200, json={"entries": [{"text": "café", "artifact": {"artifact_id": "saved"}}]})

    pc = PowerContext("http://unit-test")
    pc.client.close()
    pc.client = httpx.Client(base_url="http://unit-test", transport=httpx.MockTransport(handler))
    result = pc.remember("scope", "turn", "  cafe\u0301  ")
    assert result["changed"] is False
    assert result["records"][0]["artifact"]["artifact_id"] == "saved"
    assert calls == ["/v1/memory/entries/list"]
    pc.client.close()
