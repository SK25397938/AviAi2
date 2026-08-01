from pydantic import BaseModel

class WeatherData(BaseModel):
    icao: str
    observation_time: str
    raw_metar: str
    wind_direction: int
    wind_speed: int
    wind_gust: int | None = None
    visibility: int
    clouds: list[str]
    temperature: int
    dewpoint: int
    qnh: int
    condition: str
    flight_category: str
    source: str = "LIVE"


class RunwayDecision(BaseModel):
    active_runway: str
    arrival_runway: str
    departure_runway: str
    headwind: float
    crosswind: float
    confidence: int
    reason: str


class AtisData(BaseModel):
    information: str
    airport: str
    observation_time: str
    wind: str
    visibility: str
    weather: str
    clouds: list[str]
    temperature: int
    dewpoint: int
    qnh: int
    arrival_runway: str
    departure_runway: str
    remarks: list[str]
    message: str