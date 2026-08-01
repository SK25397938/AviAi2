from pathlib import Path
import json
from fastapi import APIRouter
router = APIRouter()

@router.get("/navigation/{icao}")
def navigation(icao: str):
    file = Path(f"data/navigation/{icao.upper()}_waypoints.json")
    if not file.exists():
        return {
            "airport": icao.upper(),
            "waypoints": []
        }
    with open(file, encoding="utf-8") as f:
        waypoints = json.load(f)
    return {
        "airport": icao.upper(),
        "waypoints": waypoints
    }