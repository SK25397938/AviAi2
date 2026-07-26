class RegionFilter:

    def __init__(self, vectors):
        self.vectors = vectors

    def inside(self, line):

        x1, y1 = line["start"]
        x2, y2 = line["end"]

        xmin = min(x1, x2)
        xmax = max(x1, x2)

        ymin = min(y1, y2)
        ymax = max(y1, y2)

        if xmax < 80:
            return False

        if xmin > 560:
            return False

        if ymax < 150:
            return False

        if ymin > 770:
            return False

        return True

    def filter(self):

        result = []

        for obj in self.vectors:

            if obj["type"] != "line":
                continue

            if self.inside(obj):
                result.append(obj)

        return result