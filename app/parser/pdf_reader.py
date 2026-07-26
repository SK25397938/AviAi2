import fitz


class PDFReader:

    def __init__(self, pdf_path):
        self.doc = fitz.open(pdf_path)

    def page_count(self):
        return len(self.doc)

    def get_page(self, page_number):
        return self.doc[page_number]

    def get_text(self, page_number):
        return self.doc[page_number].get_text()

    def get_words(self, page_number):
        return self.doc[page_number].get_text("words")

    def get_drawings(self, page_number):
        return self.doc[page_number].get_drawings()

    def get_toc(self):
        return self.doc.get_toc(simple=False)