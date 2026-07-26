import json
from typing import List, Dict
from app.core.config import redis_client


async def get_live_aircraft() -> List[Dict]:
    aircraft_list: List[Dict] = []

    for key in redis_client.scan_iter("aircraft:*"):
        data = redis_client.get(key)
        if not data:
            continue

        try:
            aircraft_list.append(json.loads(data))
        except Exception:
            continue

    return aircraft_list
