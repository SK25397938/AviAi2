import fitz
from pathlib import Path


class ChartRenderer:

    def __init__(self, pdf_path):
        self.doc = fitz.open(pdf_path)

    def render_page(self, page_index, zoom=3):
        page = self.doc.load_page(page_index)

        pix = page.get_pixmap(
            matrix=fitz.Matrix(zoom, zoom)
        )

        output = Path("output")
        output.mkdir(exist_ok=True)

        filename = output / f"page_{page_index + 1}.png"

        pix.save(filename)

        return filename

    def render_bookmark(self, title, zoom=3):

        toc = self.doc.get_toc(simple=False)

        for item in toc:

            if title in item[1]:

                page = self.doc.load_page(item[2] - 1)

                pix = page.get_pixmap(
                    matrix=fitz.Matrix(zoom, zoom)
                )

                output = Path("output")
                output.mkdir(exist_ok=True)

                filename = output / f"{title}.png"

                pix.save(filename)

                return filename

        raise Exception("Bookmark not found")