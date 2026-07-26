import json
import pandas as pd

from shapely.geometry import LineString
from shapely.geometry import Point
from shapely.geometry import Polygon


class AirportCompiler:

    def __init__(self, gdf):

        self.gdf = gdf

    def line_to_list(self, geom):

        if isinstance(geom, LineString):
            return list(geom.coords)

        return []

    def polygon_to_list(self, geom):

        if isinstance(geom, Polygon):
            return list(geom.exterior.coords)

        return []

    def point_to_tuple(self, geom):

        if isinstance(geom, Point):
            return (
                geom.x,
                geom.y
            )

        return None

    def value(self, value):

        if pd.isna(value):
            return None

        return value

    def compile(self):

        airport = {
            "runways": [],
            "taxiways": [],
            "gates": [],
            "parking_positions": [],
            "holding_positions": [],
            "aprons": []
        }

        for _, row in self.gdf.iterrows():

            typ = self.value(row.get("aeroway"))

            if typ == "runway":

                airport["runways"].append({
                    "ref": self.value(row.get("ref")),
                    "width": self.value(row.get("width")),
                    "geometry": self.line_to_list(row.geometry)
                })

            elif typ == "taxiway":

                airport["taxiways"].append({
                    "name": self.value(row.get("name")),
                    "geometry": self.line_to_list(row.geometry)
                })

            elif typ == "gate":

                airport["gates"].append({
                    "name": self.value(row.get("ref")),
                    "position": self.point_to_tuple(row.geometry)
                })

            elif typ == "parking_position":

                airport["parking_positions"].append({
                    "name": self.value(row.get("ref")),
                    "position": self.point_to_tuple(row.geometry)
                })

            elif typ == "holding_position":

                airport["holding_positions"].append({
                    "position": self.point_to_tuple(row.geometry)
                })

            elif typ == "apron":

                airport["aprons"].append({
                    "geometry": self.polygon_to_list(row.geometry)
                })

        return airport

    def save(self, path):

        with open(path, "w", encoding="utf-8") as f:

            json.dump(
                self.compile(),
                f,
                indent=4,
                allow_nan=False
            )