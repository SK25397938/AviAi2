class ProcedureManager:

    def __init__(
        self,
        stars,
        sids,
        approaches,
        holds
    ):

        self.stars = {
            p["name"].upper(): p
            for p in stars["stars"]
        }

        self.sids = {
            p["name"].upper(): p
            for p in sids["sids"]
        }

        self.approaches = {
            p["name"].upper(): p
            for p in approaches["approaches"]
        }

        self.holds = {
            p["name"].upper(): p
            for p in holds["holds"]
        }

    def get_star(self, name):

        return self.stars.get(name.upper())

    def get_sid(self, name):

        return self.sids.get(name.upper())

    def get_approach(self, name):

        return self.approaches.get(name.upper())

    def get_hold(self, name):

        return self.holds.get(name.upper())