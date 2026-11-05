from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.simulation.clock import simulation_clock
from app.simulation.world import simulation_world
from app.simulation.async_runner import SimulationRunner
from app.simulation.airport.loader import AirportLoader
from fastapi import WebSocket
import asyncio
router = APIRouter()
simulation_runner = SimulationRunner()


async def _world_state():
    return await simulation_runner.run(simulation_world.get_state)


def _holding_pattern_payload():
    manager = simulation_world.engine.holding_manager
    patterns = []
    for route in manager.holding_routes:
        waypoints = manager.racetrack_waypoints(route)
        if not waypoints:
            continue
        adaptive = route.get("adaptive_leg_length_nm")
        short_waypoints = []
        if isinstance(adaptive, dict) and adaptive.get("enabled") is True:
            try:
                short_leg = float(adaptive["short_nm"])
                standard_leg = float(route["leg_length_nm"])
                if 0 < short_leg < standard_leg:
                    short_route = dict(route)
                    short_route["leg_length_nm"] = short_leg
                    short_waypoints = manager.racetrack_waypoints(short_route)
            except (KeyError, TypeError, ValueError):
                short_waypoints = []
        if not short_waypoints:
            short_waypoints = waypoints
        patterns.append({
            "holding_route_id": route.get("id"),
            "fix": route.get("fix", route.get("trigger_waypoint")),
            "synthetic": route.get("synthetic") is True or route.get("published") is False,
            "status": route.get("status", "PUBLISHED"),
            "coordinates": [[point["longitude"], point["latitude"]] for point in waypoints],
            "short_coordinates": [[point["longitude"], point["latitude"]] for point in short_waypoints],
        })
    return {"patterns": patterns}

airport_loader = AirportLoader("VABB")


class TestSpawnRequest(BaseModel):
    session_id: str
    node: str
    route_id: str


class TestSessionRequest(BaseModel):
    session_id: str


class TestRemoveRequest(BaseModel):
    session_id: str
    callsign: str


@router.post("/test-session")
async def enter_test_session():
    try:
        return await simulation_runner.run(simulation_world.enter_test_session)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.delete("/test-session")
async def exit_test_session(request: TestSessionRequest):
    try:
        return await simulation_runner.run(simulation_world.exit_test_session, request.session_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.get("/test-session/nodes")
async def test_spawn_nodes():
    def get_nodes():
        if simulation_world.session_mode != "TEST":
            raise ValueError("Test session is not active")
        return {**simulation_world.get_session_state(), "nodes": simulation_world.engine.test_spawn_nodes()}
    try:
        return await simulation_runner.run(get_nodes)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.post("/test-session/spawn")
async def spawn_test_aircraft(request: TestSpawnRequest):
    try:
        aircraft = await simulation_runner.run(
            simulation_world.spawn_random_test_aircraft,
            request.session_id, request.node, request.route_id,
        )
        state = await simulation_runner.run(simulation_world.get_session_state)
        return {"callsign": aircraft.callsign, **state}
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.delete("/test-session/aircraft")
async def remove_test_aircraft(request: TestRemoveRequest):
    try:
        return await simulation_runner.run(
            simulation_world.remove_test_aircraft, request.session_id, request.callsign,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))
@router.get("/status")
async def simulation_status():
    return simulation_clock.get_state()

@router.get("/state")
async def simulation_state():
    return await _world_state()

@router.get("/holding-patterns")
async def holding_pattern_visualization():
    return await simulation_runner.run(_holding_pattern_payload)

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
    return await _world_state()

@router.post("/pause")
async def pause_simulation():
    simulation_clock.pause()
    return await _world_state()

@router.post("/resume")
async def resume_simulation():
    simulation_clock.resume()
    return await _world_state()

@router.post("/speed/{speed}")
async def set_simulation_speed(speed: float):
    try:
        simulation_clock.set_speed(speed)
        return await _world_state()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.websocket("/ws")
async def simulation_ws(
    websocket: WebSocket
):

    await websocket.accept()

    try:

        while True:

            state = await simulation_runner.update_and_snapshot(
                simulation_world.update, simulation_world.get_state, 1
            )
            await websocket.send_json(state)

            await asyncio.sleep(0.1)

    except Exception as e:

        import traceback

        traceback.print_exc()

        print("WS ERROR:", e)

        await websocket.close()
