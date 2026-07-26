class GeometryCleaner:

    def __init__(self, vectors):

        self.vectors = vectors

    def remove_small(self, minimum=20):

        cleaned = []

        for obj in self.vectors:

            if obj["type"] != "line":
                continue

            if obj["length"] >= minimum:

                cleaned.append(obj)

        return cleaned

    def remove_page_border(self, vectors):

        result = []

        for obj in vectors:

            x1, y1 = obj["start"]
            x2, y2 = obj["end"]

            if (
                obj["length"] > 500
            ):
                continue

            result.append(obj)

        return result

    def clean(self):

        vectors = self.remove_small()

        vectors = self.remove_page_border(vectors)

        return vectors