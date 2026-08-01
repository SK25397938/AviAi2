from abc import ABC
from abc import abstractmethod


class NavigationImporter(ABC):

    @abstractmethod
    def import_data(self):

        pass