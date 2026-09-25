class AircraftManager:

    def __init__(self):
        self.aircraft = []

    def add(self, aircraft):
        self.aircraft.append(aircraft)

    def remove(self, aircraft):
        if aircraft in self.aircraft:
            self.aircraft.remove(aircraft)

    def all(self):
        return self.aircraft