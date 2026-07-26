from math import hypot


class NeighborBuilder:

    def __init__(self, lines, tolerance=2.0):
        self.lines = lines
        self.tolerance = tolerance

    def distance(self, a, b):
        return hypot(a[0] - b[0], a[1] - b[1])

    def build(self):

        for line in self.lines:
            line["neighbors"] = []

        for i in range(len(self.lines)):

            a = self.lines[i]

            ap = [a["start"], a["end"]]

            for j in range(i + 1, len(self.lines)):

                b = self.lines[j]

                bp = [b["start"], b["end"]]

                connected = False

                for p1 in ap:
                    for p2 in bp:

                        if self.distance(p1, p2) <= self.tolerance:
                            connected = True
                            break

                    if connected:
                        break

                if connected:
                    a["neighbors"].append(j)
                    b["neighbors"].append(i)

        return self.lines