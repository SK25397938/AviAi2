from pathlib import Path
import json
from fastapi import APIRouter
import app.weather.weather_store as store
from app.weather.weather_engine import WeatherEngine
from app.weather.runway_selector import RunwaySelector
from app.weather.atis_generator import AtisGenerator

router = APIRouter()

@router.get("/weather/{icao}")
def weather(icao: str):
    airport_file = Path(f"data/airports/{icao.upper()}.json")
    with open(airport_file, encoding="utf-8") as f:
        airport = json.load(f)
    if store.live_weather:
        weather = WeatherEngine(icao).fetch()
    else:
        weather = store.custom_weather
    runway = RunwaySelector(
        weather,
        airport["runways"]
    ).select()
    atis = AtisGenerator().generate(
        weather,
        runway
    )
    return {
        "weather": weather.model_dump(),
        "runway": runway.model_dump(),
        "atis": atis.model_dump()
    }