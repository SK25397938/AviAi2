from app.navigation.database import navigation

print()

print(
    navigation.get_waypoint("EMRAK")
)

print()

print(
    navigation.get_star("EMRAK1A")
)

print()

print(
    navigation.graph.neighbors("EMRAK")
)