from app.simulation.engine import SimulationEngine

engine = SimulationEngine()

engine.spawn("A320", "AIQ432")
engine.spawn("A359", "SIA421")
engine.spawn("B77W", "UAE502")
engine.spawn("A388", "DLH757")

for step in range(250):

    engine.update()

    print(f"\nSTEP {step + 1}")

    for aircraft in engine.manager.all():

        print(
            aircraft.callsign,
            "|",
            aircraft.phase,
            "|",
            aircraft.approach_phase,
            "|",
            aircraft.state,
            "|",
            aircraft.clearance.runway,
            "|",
            round(aircraft.altitude_ft),
            "ft",
            "|",
            round(aircraft.speed_kts),
            "kt",
            "|",
            round(aircraft.heading_deg, 1)
        )