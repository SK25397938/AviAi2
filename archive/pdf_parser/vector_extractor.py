import fitz


class VectorExtractor:

    def __init__(self, pdf_path):
        self.doc = fitz.open(pdf_path)

    def page_drawings(self, page_number):
        page = self.doc.load_page(page_number)
        return page.get_drawings()

    def extract(self, page_number):

        drawings = self.page_drawings(page_number)

        vectors = []

        for drawing in drawings:

            for item in drawing["items"]:

                command = item[0]

                if command == "l":

                    start = item[1]
                    end = item[2]

                    vectors.append({

    "id": len(vectors),

    "type": "line",

    "start": (start.x, start.y),
    "end": (end.x, end.y),

    "stroke_width": drawing.get("width", 1.0),

    "stroke_color": drawing.get("color"),

    "fill_color": drawing.get("fill"),

    "line_cap": drawing.get("lineCap"),

    "line_join": drawing.get("lineJoin"),

    "dashes": drawing.get("dashes"),

    "visited": False,

    "merged": False
})

                elif command == "re":

                    rect = item[1]

                    vectors.append({

    "id": len(vectors),

    "type": "rectangle",

    "x0": rect.x0,
    "y0": rect.y0,
    "x1": rect.x1,
    "y1": rect.y1,

    "width": rect.width,
    "height": rect.height,

    "stroke_width": drawing.get("width", 1.0),

    "stroke_color": drawing.get("color"),

    "fill_color": drawing.get("fill")
})

                elif command == "qu":

                    quad = item[1]

                    vectors.append({

    "id": len(vectors),

    "type": "quad",

    "points": [

        (quad.ul.x, quad.ul.y),
        (quad.ur.x, quad.ur.y),
        (quad.lr.x, quad.lr.y),
        (quad.ll.x, quad.ll.y)

    ],

    "stroke_width": drawing.get("width", 1.0),

    "stroke_color": drawing.get("color"),

    "fill_color": drawing.get("fill")
})

                elif command == "c":

                    vectors.append({
                        "type": "curve"
                    })

        return vectors