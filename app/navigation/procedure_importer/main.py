from .resolver import WaypointResolver


def main():

    resolver = WaypointResolver()

    print()

    print(resolver.airport("VABB"))

    print()

    print(resolver.runway("VABB", "27"))

    print()

    print(resolver.vor("BBB"))

    resolver.close()


if __name__ == "__main__":

    main()