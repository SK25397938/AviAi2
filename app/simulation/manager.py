class AircraftManager:

    def __init__(self):
        self.aircraft = []

    def add(self, aircraft):
        self.aircraft.append(aircraft)

    def all(self):
        return self.aircraft