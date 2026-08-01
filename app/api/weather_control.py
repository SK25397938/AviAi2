from fastapi import APIRouter
from app.weather.weather_store import live_weather
from app.weather.weather_store import custom_weather
router = APIRouter()
@router.get("/weather/mode")
def mode():
    return {
        "mode": "LIVE" if live_weather else "CUSTOM"
    }

@router.post("/weather/live")
def live():
    import app.weather.weather_store as store
    store.live_weather = True
    return {
        "status": "LIVE"
    }

@router.post("/weather/custom")
def custom(weather: dict):
    import app.weather.weather_store as store
    store.live_weather = False
    store.custom_weather = weather
    return {
        "status": "CUSTOM"
    }