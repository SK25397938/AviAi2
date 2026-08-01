from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import asyncio
from app.api import flights, ai, simulation, semantics
from app.api.airport import router as airport_router
from app.api.weather import router as weather_router
from app.api.weather_control import router as weather_control_router
from app.api.navigation import router as navigation_router
from app.ingest.opensky_async import ingest_opensky
from app.weather.weather_loop import start as start_weather
from app.intelligence.airports import load_airports
ROOT_DIR = Path(__file__).resolve().parent.parent
app = FastAPI()
start_weather()
app.include_router(flights.router, prefix="/flights")
app.include_router(ai.router, prefix="/ai")
app.include_router(semantics.router)
app.include_router(simulation.router, prefix="/simulation")
app.include_router(airport_router)
app.include_router(weather_router)
app.include_router(navigation_router)
app.include_router(weather_control_router)
app.mount(
    "/static",
    StaticFiles(directory=ROOT_DIR / "app" / "static"),
    name="static"
)
app.mount(
    "/ui",
    StaticFiles(directory=ROOT_DIR / "static", html=True),
    name="ui"
)
app.mount(
    "/map",
    StaticFiles(directory=ROOT_DIR / "static", html=True),
    name="map"
)

@app.get("/health")
async def health():
    return {
        "status": "ok"
    }

@app.on_event("startup")
async def startup():
    load_airports("data/airports.csv")
    asyncio.create_task(
        ingest_opensky()
    )