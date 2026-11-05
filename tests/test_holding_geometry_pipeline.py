import asyncio
import ast
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.simulation.holding_manager import HoldingManager
from tests.holding_config import load_holding_routes


ROOT = Path(__file__).resolve().parents[1]
HOLDING_CONFIG = ROOT / "holding_routes.json"


class HoldingGeometryPipelineTests(unittest.TestCase):
    def setUp(self):
        self.routes = load_holding_routes(HOLDING_CONFIG)
        self.manager = HoldingManager(self.routes)

    def get_api_geometry(self):
        world = SimpleNamespace(engine=SimpleNamespace(holding_manager=self.manager))
        api_path = ROOT / "app" / "api" / "simulation.py"
        api_tree = ast.parse(api_path.read_text(encoding="utf-8"))
        helpers = [node for node in api_tree.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name in {"_holding_pattern_payload", "holding_pattern_visualization"}]
        for helper in helpers:
            helper.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=helpers, type_ignores=[]))
        async def run_in_test(function, *args, **kwargs):
            return function(*args, **kwargs)

        namespace = {
            "simulation_world": world,
            "simulation_runner": SimpleNamespace(run=run_in_test),
        }
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
                adaptive = route.get("adaptive_leg_length_nm", {})
                if adaptive.get("enabled") is True:
                    short_route = dict(route, leg_length_nm=adaptive["short_nm"])
                    short = self.manager.racetrack_waypoints(short_route)
                else:
                    short = standard
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
const backupHtml = fs.readFileSync("bakup.html", "utf8");
const match = html.match(/function updateHoldingVisualization\(holds, configuredPoints\)\{[\s\S]*?\n\}/);
assert.ok(match, "MapLibre holding source updater is present");
function layerBlock(source, id) {
  const idIndex = source.indexOf(`id:"${id}"`);
  assert.notEqual(idIndex, -1, `${id} layer exists`);
  const start = source.lastIndexOf("map.addLayer(", idIndex);
  const end = source.indexOf("});", idIndex);
  assert.notEqual(start, -1, `${id} layer declaration exists`);
  assert.notEqual(end, -1, `${id} layer declaration closes`);
  return source.slice(start, end + 3);
}
for (const [documentName, source, ids] of [
  ["main map", html, ["holding-pattern-lines", "short-holding-pattern-lines", "configured-holding-fix-markers", "configured-holding-fix-labels", "holding", "holding-labels"]],
  ["backup map", backupHtml, ["holding-pattern-lines", "configured-holding-fix-markers", "configured-holding-fix-labels", "holding", "holding-labels"]]
]) {
  assert.ok(!source.includes("SYNTHETIC / NOT PUBLISHED"), `${documentName} has no synthetic status text`);
  for (const id of ids) {
    const block = layerBlock(source, id);
    if (id === "holding-pattern-lines" || id === "short-holding-pattern-lines") {
      assert.match(block, /#e53935/i, `${documentName} active holding geometry is red`);
    } else {
      assert.doesNotMatch(block, /#(?:d17a00|d34332|b51f1a|ff0000|ff4444|e53935|ffa(?:[0-9a-f]{3})?)/i,
        `${documentName} markers retain neutral styling`);
    }
    assert.match(block, /#77818a/i, `${documentName} ${id} uses neutral grey`);
  }
}
const sources = Object.fromEntries(["holding-patterns", "short-holding-patterns", "configured-holding-fixes"]
  .map(id => [id, {data: null, setData(data) { this.data = data; }}]));
const map = {getSource(id) { return sources[id]; }};
const update = new Function("map", "configuredHoldingPatterns", `let lastHoldingVisualizationSignature=null; ${match[0]}; return updateHoldingVisualization;`)
  (map, input.patterns);
update([], []);
assert.equal(sources["holding-patterns"].data.features.length, input.patterns.length, "one standard feature per holding pattern");
assert.equal(sources["short-holding-patterns"].data.features.length, input.patterns.length, "one short feature per holding pattern");
for (const pattern of input.patterns) {
  const standard = sources["holding-patterns"].data.features.find(f => f.properties.holding_route_id === pattern.holding_route_id);
  const short = sources["short-holding-patterns"].data.features.find(f => f.properties.holding_route_id === pattern.holding_route_id);
  assert.deepEqual(standard.geometry.coordinates, pattern.coordinates, `${pattern.holding_route_id} standard GeoJSON input`);
  assert.deepEqual(short.geometry.coordinates, pattern.short_coordinates, `${pattern.holding_route_id} short GeoJSON input`);
  assert.equal(standard.properties.synthetic, pattern.synthetic === true, `${pattern.holding_route_id} standard synthetic style metadata`);
  assert.equal(short.properties.synthetic, pattern.synthetic === true, `${pattern.holding_route_id} short synthetic style metadata`);
  assert.equal(short.properties.active, false, `${pattern.holding_route_id} inactive circuit starts neutral`);
}
const active = input.patterns.map(pattern => ({
  holding_route_id: pattern.holding_route_id,
  waypoints: pattern.short_coordinates.map(([longitude, latitude]) => ({longitude, latitude}))
}));
const configuredPoints = input.patterns.map(pattern => ({
  holding_route_id: pattern.holding_route_id,
  fix: pattern.fix,
  longitude: pattern.coordinates[0][0],
  latitude: pattern.coordinates[0][1],
  active: true
}));
update(active, configuredPoints);
assert.equal(sources["short-holding-patterns"].data.features.length, input.patterns.length, "active updates replace rather than duplicate features");
for (const pattern of input.patterns) {
  const feature = sources["short-holding-patterns"].data.features.find(f => f.properties.holding_route_id === pattern.holding_route_id);
  assert.deepEqual(feature.geometry.coordinates, pattern.short_coordinates, `${pattern.holding_route_id} active short-hold GeoJSON input`);
  const standard = sources["holding-patterns"].data.features.find(f => f.properties.holding_route_id === pattern.holding_route_id);
  assert.ok(feature.properties.active || standard.properties.active,
    `${pattern.holding_route_id} active circuit is highlighted on its rendered geometry`);
  const marker = sources["configured-holding-fixes"].data.features.find(f => f.properties.holding_route_id === pattern.holding_route_id);
  assert.ok(marker, `${pattern.holding_route_id} marker is recreated after an update`);
  assert.equal(marker.properties.synthetic, pattern.synthetic === true, `${pattern.holding_route_id} recreated marker remains grey-styled`);
}
if (input.patterns.length > 1) {
  for (const selected of input.patterns.slice(0, 2)) {
    const oneActiveHold = [{
      holding_route_id: selected.holding_route_id,
      waypoints: selected.short_coordinates.map(([longitude, latitude]) => ({longitude, latitude}))
    }];
    update(oneActiveHold, []);
    for (const pattern of input.patterns) {
      const standard = sources["holding-patterns"].data.features.find(f => f.properties.holding_route_id === pattern.holding_route_id);
      const short = sources["short-holding-patterns"].data.features.find(f => f.properties.holding_route_id === pattern.holding_route_id);
      assert.equal(!!(standard.properties.active || short.properties.active),
        pattern.holding_route_id === selected.holding_route_id,
        "changing assignments transfers the highlight only to the newly active pattern");
    }
  }
}
update([], []);
assert.ok(sources["holding-patterns"].data.features.every(feature => !feature.properties.active),
  "released circuits lose their active highlight");
assert.ok(sources["short-holding-patterns"].data.features.every(feature => !feature.properties.active),
  "released short circuits lose their active highlight");
console.log("MapLibre geometry input and synthetic presentation metadata survive initial and active updates");
'''
        result = subprocess.run(
            [node, "-e", render_check], input=json.dumps(api_data), text=True,
            capture_output=True, cwd=ROOT, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
