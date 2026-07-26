import aiohttp
import asyncio
import json

from app.ingest.opensky_auth import get_access_token
from app.intelligence.trajectory import update_trajectory, analyze_vertical_trend
from app.intelligence.behavior import classify_behavior
from app.intelligence.event_engine import classify_event
from app.intelligence.route_engine import infer_route
from app.core.config import redis_client

OPENSKY_URL = "https://opensky-network.org/api/states/all"


async def ingest_opensky():
    print("[INGESTION] Started")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                token = await get_access_token()
                headers = {"Authorization": f"Bearer {token}"}

                async with session.get(OPENSKY_URL, headers=headers, timeout=15) as resp:
                    if resp.status == 429:
                        print("[INGESTION] Rate limited — backing off")
                        await asyncio.sleep(30)
                        continue

                    resp.raise_for_status()
                    data = await resp.json()

                states = data.get("states") or []

                for idx, s in enumerate(states):
                    if s[5] is None or s[6] is None:
                        continue

                    icao = s[0]
                    altitude = s[7]
                    speed = s[9]
                    heading = s[10]

                    vertical_state = "UNKNOWN"
                    behavior = "INSUFFICIENT_DATA"

                    if altitude is not None and speed is not None:
                        trajectory = update_trajectory(icao, altitude)

                        if trajectory:
                            vertical_state = analyze_vertical_trend(trajectory)

                        behavior = classify_behavior(
                            altitude,
                            speed,
                            vertical_state
                        )

                    aircraft = {
                        "icao24": icao,
                        "callsign": s[1].strip() if s[1] else None,
                        "latitude": s[6],
                        "longitude": s[5],
                        "baro_altitude_m": altitude,
                        "velocity_mps": speed,
                        "heading_deg": heading,
                        "vertical_state": vertical_state,
                        "behavior": behavior
                    }

                    aircraft["event"] = classify_event(aircraft)

                    # ✅ ROUTE INFERENCE (CORRECT CONTRACT)
                    origin, destination = None, None

                    if vertical_state in (
                        "ASCENDING",
                        "RAPID_CLIMB",
                        "DESCENDING",
                        "RAPID_DESCENT",
                    ):
                        origin, destination = infer_route(
                            aircraft["latitude"],
                            aircraft["longitude"],
                            vertical_state,
                        )

                    if origin or destination:
                        aircraft["route"] = {
                            "from": origin,
                            "to": destination
                        }

                    redis_client.setex(
                        f"aircraft:{icao}",
                        10,
                        json.dumps(aircraft)
                    )

                    if idx % 200 == 0:
                        await asyncio.sleep(0)

                print(f"[INGESTION] Updated {len(states)} aircraft")

            except asyncio.TimeoutError:
                print("[INGESTION ERROR] OpenSky timeout — retrying...")
                await asyncio.sleep(5)

            except Exception as e:
                print("[INGESTION ERROR]", repr(e))
                await asyncio.sleep(10)

            await asyncio.sleep(0.5)  