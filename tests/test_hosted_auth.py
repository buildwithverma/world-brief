import httpx
import pytest
from fastapi.testclient import TestClient
from backend import auth


@pytest.mark.asyncio
async def test_auth_owner_and_invalid_tokens(monkeypatch):
    original_client=httpx.AsyncClient
    monkeypatch.setattr(auth,"OWNER_USER_ID","owner")
    monkeypatch.setattr(auth,"SUPABASE_URL","https://auth.example.com")
    def handler(request):
        token=request.headers["authorization"]
        if token=="Bearer expired":
            return httpx.Response(401)
        return httpx.Response(200,json={"id":"owner" if token=="Bearer owner" else "other"})
    monkeypatch.setattr(auth.httpx,"AsyncClient",lambda **kwargs:original_client(transport=httpx.MockTransport(handler),**kwargs))
    assert await auth.authorized("Bearer owner")
    assert not await auth.authorized("Bearer other")
    assert not await auth.authorized("Bearer expired")
    assert not await auth.authorized(None)


def test_hosted_routes_require_owner_and_reject_key_writes(monkeypatch):
    from backend import main
    monkeypatch.setattr(main,"HOSTED",True)
    monkeypatch.setattr(main,"ORIGINS",["https://brief.example.com"])
    async def allowed(header):return header=="Bearer owner"
    monkeypatch.setattr(main,"authorized",allowed)
    client=TestClient(main.app)
    assert client.get('/healthz').status_code==200
    assert client.get('/api/preferences').status_code==401
    assert client.get('/api/preferences',headers={"Authorization":"Bearer owner"}).status_code==200
    assert client.get('/api/preferences',headers={"Authorization":"Bearer owner","Origin":"https://evil.example.com"}).status_code==403
    assert client.post('/api/settings/groq',headers={"Authorization":"Bearer owner"},json={"key":"fake-key-for-test-only"}).status_code==403
