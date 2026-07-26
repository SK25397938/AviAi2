from fastapi import APIRouter
from app.core.config import redis_client
import json
from collections import Counter

router = APIRouter()

@router.get("/snapshot")
def airspace_snapshot():
    keys = redis_client.keys("aircraft:*")

    behaviors = Counter()
    events = []
    aircraft = []

    for key in keys:
        raw = redis_client.get(key)
        if not raw:
            continue

        ac = json.loads(raw)

        behavior = ac.get("behavior", "UNKNOWN")
        event = ac.get("event", "UNKNOWN")

        behaviors[behavior] += 1
        aircraft.append(ac)

        if event not in ["NORMAL_CRUISE", "NORMAL_OPERATION", "UNKNOWN"]:
            events.append({
                "callsign": ac.get("callsign") or ac.get("icao24"),
                "event": event,
                "from": ac.get("route", {}).get("from"),
                "to": ac.get("route", {}).get("to")
            })

    return {
        "total": len(aircraft),
        "behaviors": dict(behaviors),
        "events": events,
        "aircraft": aircraft
    }
