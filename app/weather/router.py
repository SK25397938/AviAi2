from fastapi import APIRouter

from .weather_engine import WeatherEngine
from .runway_selector import RunwaySelector
from .atis_generator import AtisGenerator

router = APIRouter()

weather_engine = WeatherEngine()
runway_selector = RunwaySelector()
atis_generator = AtisGenerator()

@router.get("/weather")
def weather():

    weather = weather_engine.current()

    runway = runway_selector.choose(weather)

    atis = atis_generator.generate(weather, runway)

    return {
        "weather": weather,
        "runway": runway,
        "atis": atis
    }