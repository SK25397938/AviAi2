import asyncio
import ast
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.simulation.holding_manager import HoldingManager


ROOT = Path(__file__).resolve().parents[1]
HOLDING_CONFIG = ROOT / "holding_routes.json"


class HoldingGeometryPipelineTests(unittest.TestCase):
    def setUp(self):
        with HOLDING_CONFIG.open(encoding="utf-8") as source:
            self.routes = json.load(source)["holding_routes"]
        self.manager = HoldingManager(self.routes)

    def get_api_geometry(self):
        world = SimpleNamespace(engine=SimpleNamespace(holding_manager=self.manager))
        api_path = ROOT / "app" / "api" / "simulation.py"
        api_tree = ast.parse(api_path.read_text(encoding="utf-8"))
        handler = next(node for node in api_tree.body
                       if isinstance(node, ast.AsyncFunctionDef)
                       and node.name == "holding_pattern_visualization")
        handler.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
        namespace = {"simulation_world": world}
        exec(compile(module, str(api_path), "exec"), namespace)
        return asyncio.run(namespace["holding_pattern_visualization"]())

    def test_api_and_maplibre_render_input_preserve_generated_geometry(self):
        api_data = self.get_api_geometry()
        self.assertEqual(len(api_data["patterns"]), len(self.routes))
        by_id = {pattern["holding_route_id"]: pattern for pattern in api_data["patterns"]}

        for route in self.routes:
            with self.subTest(route=route["id"]):
                pattern = by_id[route["id"]]
                standard = self.manager.racetrack_waypoints(route)
                short_route = dict(route, leg_length_nm=route["adaptive_leg_length_nm"]["short_nm"])
                short = self.manager.racetrack_waypoints(short_route)
                self.assertEqual(pattern["coordinates"],
                                 [[point["longitude"], point["latitude"]] for point in standard])
                self.assertEqual(pattern["short_coordinates"],
                                 [[point["longitude"], point["latitude"]] for point in short])
                aircraft = SimpleNamespace(speed_kts=route["max_speed_kt"])
                guidance_short = self.manager._selected_holding_geometry(route, aircraft, 0.0)
                guidance_standard = self.manager._selected_holding_geometry(
                    route, aircraft, 1_000_000_000.0
                )
                self.assertEqual(pattern["short_coordinates"],
                                 [[point["longitude"], point["latitude"]]
                                  for point in guidance_short])
                self.assertEqual(pattern["coordinates"],
                                 [[point["longitude"], point["latitude"]]
                                  for point in guidance_standard])

                # The configured waypoint order is fix, outbound-turn end,
                # outbound-leg end, inbound-turn end, fix.
                anchors = (0, self.manager.ARC_SEGMENTS, self.manager.ARC_SEGMENTS + 1,
                           self.manager.ARC_SEGMENTS * 2 + 1, -1)
                for configured, index in zip(route["waypoints"], anchors):
                    self.assertLessEqual(
                        self.manager._distance_nm(configured, standard[index]), 0.5,
                        f"{route['id']} configured anchor {configured['name']} differs from generated geometry",
                    )

        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required to execute the MapLibre data-source update function")
        render_check = r'''
const fs = require("node:fs");
const assert = require("node:assert/strict");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const html = fs.readFileSync("static/sim.html", "utf8");
const match = html.match(/function updateHoldingVisualization\(holds, configuredPoints\)\{[\s\S]*?\n\}/);
assert.ok(match, "MapLibre holding source updater is present");
const sources = Object.fromEntries(["holding-patterns", "short-holding-patterns", "configured-holding-fixes"]
  .map(id => [id, {data: null, setData(data) { this.data = data; }}]));
const map = {getSource(id) { return sources[id]; }};
const update = new Function("map", "configuredHoldingPatterns", `${match[0]}; return updateHoldingVisualization;`)
  (map, input.patterns);
update([], []);
assert.equal(sources["holding-patterns"].data.features.length, input.patterns.length, "one standard feature per holding pattern");
assert.equal(sources["short-holding-patterns"].data.features.length, input.patterns.length, "one short feature per holding pattern");
for (const pattern of input.patterns) {
  const standard = sources["holding-patterns"].data.features.find(f => f.properties.holding_route_id === pattern.holding_route_id);
  const short = sources["short-holding-patterns"].data.features.find(f => f.properties.holding_route_id === pattern.holding_route_id);
  assert.deepEqual(standard.geometry.coordinates, pattern.coordinates, `${pattern.holding_route_id} standard GeoJSON input`);
  assert.deepEqual(short.geometry.coordinates, pattern.short_coordinates, `${pattern.holding_route_id} short GeoJSON input`);
}
const active = input.patterns.map(pattern => ({
  holding_route_id: pattern.holding_route_id,
  waypoints: pattern.short_coordinates.map(([longitude, latitude]) => ({longitude, latitude}))
}));
update(active, []);
assert.equal(sources["short-holding-patterns"].data.features.length, input.patterns.length, "active updates replace rather than duplicate features");
for (const pattern of input.patterns) {
  const feature = sources["short-holding-patterns"].data.features.find(f => f.properties.holding_route_id === pattern.holding_route_id);
  assert.deepEqual(feature.geometry.coordinates, pattern.short_coordinates, `${pattern.holding_route_id} active short-hold GeoJSON input`);
}
console.log("MapLibre geometry input matches the API for standard, configured short, and active short patterns");
'''
        result = subprocess.run(
            [node, "-e", render_check], input=json.dumps(api_data), text=True,
            capture_output=True, cwd=ROOT, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
