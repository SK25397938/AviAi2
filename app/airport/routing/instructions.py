class TaxiInstructionBuilder:

    def __init__(self, graph, semantics):

        self.graph = graph

        self.semantics = semantics

    def taxiways_for_edges(self, edge_ids):

        names = []

        for taxiway in self.semantics.get("taxiways", []):

            if any(edge in taxiway["edges"] for edge in edge_ids):

                names.append(taxiway["name"])

        return names

    def build(self, route):

        taxiways = self.taxiways_for_edges(

            route["edges"]

        )

        if not taxiways:

            return {

                "taxiways": [],

                "instruction": "No named taxiway available."

            }

        ordered = []

        for name in taxiways:

            if name not in ordered:

                ordered.append(name)

        return {

            "taxiways": ordered,

            "instruction": "Taxi via " + ", ".join(ordered) + "."

        }