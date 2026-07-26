from fastapi import APIRouter, HTTPException
from app.simulation.clock import simulation_clock
from app.simulation.world import simulation_world
from app.simulation.airport.loader import AirportLoader

router = APIRouter()

airport_loader = AirportLoader("VABB")

@router.get("/status")
async def simulation_status():
    return simulation_clock.get_state()

@router.get("/state")
async def simulation_state():
    return simulation_world.get_state()

@router.get("/airport")
async def simulation_airport():
    airport = airport_loader.load()

    return {
        "icao": airport.icao,
        "iata": airport.iata,
        "name": airport.name,
        "latitude": airport.latitude,
        "longitude": airport.longitude,
        "elevation_ft": airport.elevation_ft,
        "runways": [
            {
                "id": runway.id,
                "designator": runway.designator,
                "opposite_designator": runway.opposite_designator,
                "length_m": runway.length_m,
                "width_m": runway.width_m,
                "surface": runway.surface,
                "coordinates": runway.coordinates,
                "active": runway.active
            }
            for runway in airport.runways
        ],
        "taxiways": [
            {
                "id": taxiway.id,
                "name": taxiway.name,
                "coordinates": taxiway.coordinates,
                "direction": taxiway.direction,
                "status": taxiway.status
            }
            for taxiway in airport.taxiways
        ],
        "stands": [
            {
                "id": stand.id,
                "name": stand.name,
                "latitude": stand.latitude,
                "longitude": stand.longitude,
                "terminal": stand.terminal,
                "status": stand.status
            }
            for stand in airport.stands
        ],
        "holding_points": [
            {
                "id": point.id,
                "name": point.name,
                "latitude": point.latitude,
                "longitude": point.longitude,
                "runway": point.runway,
                "taxiway": point.taxiway
            }
            for point in airport.holding_points
        ]
    }

@router.post("/start")
async def start_simulation():
    simulation_clock.start()
    return simulation_world.get_state()

@router.post("/pause")
async def pause_simulation():
    simulation_clock.pause()
    return simulation_world.get_state()

@router.post("/resume")
async def resume_simulation():
    simulation_clock.resume()
    return simulation_world.get_state()

@router.post("/speed/{speed}")
async def set_simulation_speed(speed: float):
    try:
        simulation_clock.set_speed(speed)
        return simulation_world.get_state()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))