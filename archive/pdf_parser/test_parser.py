from app.parser.vector_extractor import VectorExtractor
from app.parser.geometry_classifier import GeometryClassifier
from app.parser.geometry_cleaner import GeometryCleaner
from app.parser.neighbor_builder import NeighborBuilder
from app.parser.component_builder import ComponentBuilder


PDF = "data/charts/VABB.pdf"
PAGE = 77


extractor = VectorExtractor(PDF)

vectors = extractor.extract(PAGE)

classifier = GeometryClassifier(vectors)

groups = classifier.classify()

cleaner = GeometryCleaner(
    groups["runways"] +
    groups["taxiways"]
)

cleaned = cleaner.clean()

builder = NeighborBuilder(cleaned)

cleaned = builder.build()

component_builder = ComponentBuilder(cleaned)

components = component_builder.build()

print("=" * 70)
print("VABB PARSER REPORT")
print("=" * 70)

print()

print("RAW DATA")
print("-" * 70)

print("Total Vectors        :", len(vectors))

print()

print("CLASSIFICATION")
print("-" * 70)

print("Runway Candidates    :", len(groups["runways"]))
print("Taxiway Candidates   :", len(groups["taxiways"]))
print("Small Geometry       :", len(groups["small"]))

print()

print("CLEANING")
print("-" * 70)

print(
    "Before Cleaning      :",
    len(groups["runways"]) + len(groups["taxiways"])
)

print(
    "After Cleaning       :",
    len(cleaned)
)

print()

print("NEIGHBOR GRAPH")
print("-" * 70)

neighbor_counts = [
    len(line["neighbors"])
    for line in cleaned
]

print(
    "Maximum Neighbors    :",
    max(neighbor_counts)
)

print(
    "Average Neighbors    :",
    round(
        sum(neighbor_counts) /
        len(neighbor_counts),
        2
    )
)

print(
    "Isolated Lines       :",
    sum(
        1
        for c in neighbor_counts
        if c == 0
    )
)

print()

print("CONNECTED COMPONENTS")
print("-" * 70)

sizes = sorted(
    [len(c) for c in components],
    reverse=True
)

print(
    "Total Components     :",
    len(components)
)

print()

print("Largest Components")

for i, size in enumerate(
    sizes[:20],
    start=1
):

    print(
        f"{i:02d}. {size} lines"
    )

print()

largest = max(
    components,
    key=len
)

print(
    "Largest Component    :",
    len(largest),
    "lines"
)

print()

print("TOP 25 LONGEST LINES")
print("-" * 70)

for obj in sorted(
    cleaned,
    key=lambda x: x["length"],
    reverse=True
)[:25]:

    print(
        f"{obj['length']:8.2f}px | "
        f"{obj['angle']:7.2f}° | "
        f"N={len(obj['neighbors']):2d} | "
        f"{obj['start']} -> {obj['end']}"
    )

print()

print("SAMPLE VECTOR")
print("-" * 70)

sample = max(
    cleaned,
    key=lambda x: x["length"]
)

print(sample)

print()

print("=" * 70)
print("END OF REPORT")
print("=" * 70)