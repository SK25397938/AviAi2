from pathlib import Path

from fastapi import APIRouter
from fastapi import Body
from pydantic import BaseModel

from app.airport.semantic_editor import SemanticEditor
from app.airport.graph.builder import AirportGraphBuilder
from app.airport.routing.astar import AStar
from app.airport.routing.instructions import TaxiInstructionBuilder

router = APIRouter()


class TaxiwayRequest(BaseModel):

    edge_ids: list[int]

    name: str


class HoldingPointRequest(BaseModel):

    name: str

    node: int


@router.post("/semantics/{icao}/edge")
def rename_edge(

    icao: str,

    edge_id: int = Body(...),

    name: str = Body(...)

):

    return SemanticEditor(

        icao

    ).rename_edge(

        edge_id,

        name

    )


@router.get("/semantics/{icao}")
def get_semantics(

    icao: str

):

    return SemanticEditor(

        icao

    ).get()


@router.post("/semantics/{icao}/taxiway")
def add_taxiway(

    icao: str,

    request: TaxiwayRequest

):

    return SemanticEditor(

        icao

    ).add_taxiway(

        request.edge_ids,

        request.name

    )


@router.post("/semantics/{icao}/holding-point")
def add_holding_point(

    icao: str,

    request: HoldingPointRequest

):

    return SemanticEditor(

        icao

    ).add_holding_point(

        request.name,

        request.node

    )


@router.get("/semantics/{icao}/route")
def get_route(

    icao: str,

    start: int,

    end: int

):

    airport = Path(

        f"data/airports/{icao.upper()}.json"

    )

    graph = AirportGraphBuilder(

        airport

    ).build()

    astar = AStar(

        graph

    )

    route = astar.route(

        start,

        end

    )

    semantics = SemanticEditor(

        icao

    ).get()

    instruction = TaxiInstructionBuilder(

        graph,

        semantics

    ).build(

        route

    )

    route["taxiways"] = instruction["taxiways"]

    route["instruction"] = instruction["instruction"]

    return route


@router.put("/semantics/{icao}/taxiway/{index}")
def update_taxiway(

    icao: str,

    index: int,

    request: TaxiwayRequest

):

    return SemanticEditor(

        icao

    ).update_taxiway(

        index,

        request.edge_ids,

        request.name

    )


@router.delete("/semantics/{icao}/taxiway/{index}")
def delete_taxiway(

    icao: str,

    index: int

):

    return SemanticEditor(

        icao

    ).delete_taxiway(

        index

    )