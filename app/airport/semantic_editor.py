from app.airport.semantic_store import SemanticStore

class SemanticEditor:
    def __init__(self, icao):
        self.store = SemanticStore(icao)

    def get(self):
        return self.store.load()

    def add_taxiway(self, edge_ids, name):
        data = self.store.load()
        data["taxiways"].append({
            "name": name,
            "edges": edge_ids
        })
        self.store.save(data)
        return data

    def update_taxiway(self, index, edge_ids, name):
        data = self.store.load()
        data["taxiways"][index] = {
            "name": name,
            "edges": edge_ids
        }
        self.store.save(data)
        return data

    def delete_taxiway(self, index):
        data = self.store.load()
        data["taxiways"].pop(index)
        self.store.save(data)
        return data

    def rename_edge(self, edge_id, name):
        data = self.store.load()
        for taxiway in data["taxiways"]:
            if edge_id in taxiway["edges"]:
                taxiway["name"] = name
                self.store.save(data)
                return taxiway
        taxiway = {
            "name": name,
            "edges": [edge_id]
        }
        data["taxiways"].append(taxiway)
        self.store.save(data)
        return taxiway

    def add_holding_point(self, name, node):

        data = self.store.load()

        for hp in data["holding_points"]:

          if hp["name"] == name:

            hp["node"] = node

            self.store.save(data)

            return data

        data["holding_points"].append({

        "name": name,

        "node": node

    })

        self.store.save(data)

        return data