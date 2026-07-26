from app.parser.pdf_reader import PDFReader


class PageLocator:

    def __init__(self, pdf_path):
        self.reader = PDFReader(pdf_path)

    def find_section(self, keyword):
        keyword = keyword.upper()

        for page in range(self.reader.page_count()):
            text = self.reader.get_text(page).upper()

            if keyword in text:
                return page

        return None

    def find_sections(self, keywords):
        pages = {}

        for keyword in keywords:
            page = self.find_section(keyword)

            if page is not None:
                pages[keyword] = page

        return pages