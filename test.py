from app.data.airport_loader import load_airports
from app.intelligence.nearest_airport import find_nearest_airport

load_airports()

airport, distance = find_nearest_airport(28.6, 77.2)

print(airport)
print("Distance (km):", round(distance, 2))
