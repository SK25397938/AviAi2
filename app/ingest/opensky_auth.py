import aiohttp
import time
from app.core.config import OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

_access_token = None
_token_expiry = 0


async def get_access_token():
    global _access_token, _token_expiry

    if _access_token and time.time() < _token_expiry - 30:
        return _access_token

    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": OPENSKY_CLIENT_ID,
                "client_secret": OPENSKY_CLIENT_SECRET,
            },
            timeout=20,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

    _access_token = data["access_token"]
    _token_expiry = time.time() + data.get("expires_in", 300)

    return _access_token
print("CLIENT_ID LOADED:", bool(OPENSKY_CLIENT_ID))
print("CLIENT_SECRET LOADED:", bool(OPENSKY_CLIENT_SECRET))
