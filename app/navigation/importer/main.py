from .download import Downloader
from .builder import DatabaseBuilder


def main():

    Downloader().download()

    builder = DatabaseBuilder()

    builder.build_airports()

    builder.build_navaids()

    builder.build_runways()

    builder.close()

    print()

    print("WORLD NAVIGATION DATABASE READY")


if __name__ == "__main__":

    main()