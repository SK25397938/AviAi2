import osmnx as ox


class AirportImporter:

    def __init__(self, icao):

        self.icao = icao.upper()

    def download(self):

        tags = {
            "aeroway": True
        }

        gdf = ox.features_from_place(
            f"{self.icao} Airport",
            tags
        )

        return gdf