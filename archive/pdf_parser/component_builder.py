class ComponentBuilder:

    def __init__(self, lines):
        self.lines = lines
        self.components = []

    def dfs(self, index, visited, component):

        visited.add(index)

        component.append(index)

        for n in self.lines[index]["neighbors"]:

            if n not in visited:
                self.dfs(n, visited, component)

    def build(self):

        visited = set()

        for i in range(len(self.lines)):

            if i in visited:
                continue

            component = []

            self.dfs(
                i,
                visited,
                component
            )

            self.components.append(component)

        return self.components