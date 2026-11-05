const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "..", "static", "sim.html"), "utf8");
const backupHtml = fs.readFileSync(path.join(__dirname, "..", "bakup.html"), "utf8");

test("OLGUS is added as a standalone map waypoint at its project navigation coordinates", () => {
  for (const [pageName, page] of [["main map", html], ["backup map", backupHtml]]) {
    const match = page.match(/function appendMapReferenceWaypoints\(waypointFeatures\)\{[\s\S]*?\n\}/);
    assert.ok(match, `${pageName} map-only waypoint helper exists`);
    const append = new Function(`${match[0]}; return appendMapReferenceWaypoints;`)();
    const features = [];
    append(features);

    assert.deepEqual(features, [{
      type: "Feature",
      properties: {name: "OLGUS", route_id: "NAVIGATION_REFERENCE"},
      geometry: {type: "Point", coordinates: [73.473333, 19.215]}
    }]);
    assert.match(page, /appendMapReferenceWaypoints\(waypointFeatures\)/,
      `${pageName} route visualization includes the standalone waypoint`);
  }
});

