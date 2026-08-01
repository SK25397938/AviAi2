class AirportGraph:

    def __init__(self):

        self.nodes = {}

    def add_node(self, name, lat, lon):

        self.nodes[name] = {

            "lat": lat,

            "lon": lon,

            "connections": []
        }

    def connect(self, a, b):

        self.nodes[a]["connections"].append(b)