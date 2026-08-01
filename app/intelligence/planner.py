from collections import deque


def shortest_path(graph, start, goal):

    queue = deque()

    queue.append((start, [start]))

    visited = {start}

    while queue:

        node, path = queue.popleft()

        if node == goal:

            return path

        for neighbor in graph.neighbors(node):

            if neighbor not in visited:

                visited.add(neighbor)

                queue.append(

                    (

                        neighbor,

                        path + [neighbor]

                    )

                )

    return []