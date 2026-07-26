from app.parser.pdf_reader import PDFReader


class TOCParser:

    def __init__(self, pdf_path):
        self.reader = PDFReader(pdf_path)

    def get_toc(self):
        return self.reader.doc.get_toc()