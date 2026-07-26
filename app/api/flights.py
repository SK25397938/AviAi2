import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.config import redis_client

router = APIRouter()


def normalize_lon(lon: float) -> float:
    """Normalize longitude to [-180, 180]"""
    while lon > 180:
        lon -= 360
    while lon < -180:
        lon += 360
    return lon


@router.websocket("/ws")
async def flights_ws(websocket: WebSocket):
    await websocket.accept()

    previous_positions = {}
    first_snapshot = True

    params = websocket.query_params
    north = float(params.get("north", 90))
    south = float(params.get("south", -90))
    east = normalize_lon(float(params.get("east", 180)))
    west = normalize_lon(float(params.get("west", -180)))

    try:
        while True:
            updates = []
            seen_ids = set()

            for key in redis_client.scan_iter("aircraft:*"):
                data = redis_client.get(key)
                if not data:
                    continue

                try:
                    ac = json.loads(data)
                except json.JSONDecodeError:
                    continue

                lat = ac.get("latitude")
                lon = ac.get("longitude")

                if lat is None or lon is None:
                    continue

                lon = normalize_lon(lon)

                if not (south <= lat <= north and west <= lon <= east):
                    continue

                icao = ac["icao24"]
                seen_ids.add(icao)
                current = (lat, lon)

                payload = {
    "icao24": icao,
    "callsign": ac.get("callsign", "").strip(),

    "lat": lat,
    "lon": lon,

    "heading": ac.get("heading_deg", 0),

    "speed": round(ac.get("velocity_mps", 0) * 1.94384, 1),

    "altitude": round(ac.get("baro_altitude_m", 0) * 3.28084),

    "type": ac.get("aircraft_type", "UNKNOWN"),

    "phase": ac.get("flight_phase", "UNKNOWN")
}

                if first_snapshot:
                    previous_positions[icao] = current
                    updates.append(payload)
                    continue

                if previous_positions.get(icao) != current:
                    previous_positions[icao] = current
                    updates.append(payload)

            previous_positions = {
                k: v for k, v in previous_positions.items() if k in seen_ids
            }

            if updates:
                await websocket.send_json(updates)

            first_snapshot = False
            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print("WebSocket error:", e)
