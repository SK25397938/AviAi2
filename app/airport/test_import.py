from app.airport.importer import AirportImporter
from app.airport.compiler import AirportCompiler
from app.airport.exporter import AirportExporter

airport = AirportImporter("VABB")

gdf = airport.download()

compiler = AirportCompiler(gdf)

compiled = compiler.compile()

exporter = AirportExporter(compiled)

path = exporter.save("VABB")

print()

print("=" * 60)

print("AIRPORT COMPILED")

print("=" * 60)

print()

print("Runways :", len(compiled["runways"]))

print("Taxiways :", len(compiled["taxiways"]))

print("Parking :", len(compiled["parking_positions"]))

print("Gates :", len(compiled["gates"]))

print("Holding :", len(compiled["holding_positions"]))

print("Aprons :", len(compiled["aprons"]))

print()

print("Saved :", path)