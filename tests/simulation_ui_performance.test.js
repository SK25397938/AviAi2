const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const page = fs.readFileSync(path.join(__dirname, "..", "static", "sim.html"), "utf8");

function instructionRenderer() {
  const match = page.match(/function renderInstructionLog\(log\)\s*\{[\s\S]*?\n\}/);
  assert.ok(match, "instruction renderer exists");
  const body = {writes: 0, html: ""};
  Object.defineProperty(body, "innerHTML", {
    get() { return this.html; },
    set(value) { this.writes++; this.html = value; }
  });
  const context = {
    document: {getElementById: () => body},
    escapeLogText: value => String(value == null ? "" : value)
      .replace(/[&<>"']/g, ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"})[ch]),
    lastInstructionLogSignature: null
  };
  vm.runInNewContext(match[0], context);
  return {render: context.renderInstructionLog, body};
}

test("500-entry WebSocket instruction snapshots use one DOM write and skip repeats", () => {
  const {render, body} = instructionRenderer();
  const entries = Array.from({length: 500}, (_, index) => ({
    time: `12:00:${String(index % 60).padStart(2, "0")}`,
    callsign: `AC${index}`,
    controller: "Arrival",
    instruction: `Proceed to FIX${index}`
  }));

  render(entries);
  assert.equal(body.writes, 1);
  assert.equal((body.html.match(/class="log-entry"/g) || []).length, 500);
  render(entries);
  assert.equal(body.writes, 1, "unchanged snapshots do not reparse all log rows");

  const next = entries.slice(1).concat({
    time: "12:01:00", callsign: "AC500", controller: "Arrival", instruction: "Proceed to APP32"
  });
  render(next);
  assert.equal(body.writes, 2, "a changed capped log refreshes once");
  assert.match(body.html, /Proceed to APP32/);
});

test("WebSocket handler does not dump full aircraft payloads to the console", () => {
  assert.doesNotMatch(page, /console\.log\([\s\S]{0,100}"Aircraft received:"[\s\S]{0,100}acData/);
});

test("aircraft guidance is indexed and submitted once per WebSocket map update", () => {
  const update = page.match(/function updateAircraftMarkers\(\)\s*\{[\s\S]*?\n\}/);
  const guidance = page.match(/function updateGuidance\(ac\)\s*\{[\s\S]*?\n\}/);
  assert.ok(update && guidance, "aircraft update functions exist");
  assert.match(update[0], /new Map\(acData\.map/);
  assert.match(page, /new Map\(airportGraph\.nodes\.map/);
  assert.match(page, /new Map\(points\.map/);
  assert.equal((update[0].match(/\.setData\(/g) || []).length, 1,
    "guidance source receives one full collection after all simulated aircraft are processed");
  assert.doesNotMatch(guidance[0], /\.setData\(/,
    "per-aircraft guidance calculation does not submit a full GeoJSON source");
});

test("the animation frame does not rewrite static WebSocket marker positions", () => {
  const tick = page.match(/async function tick\(now\)\s*\{[\s\S]*?\n\}/);
  assert.ok(tick, "animation loop exists");
  assert.doesNotMatch(tick[0], /interpolateAircraftMarkers|setLngLat/);
  assert.match(page, /new URLSearchParams\(location\.search\)\.has\("perf"\)/,
    "detailed performance reporting is opt-in");
  assert.match(page, /\["zoomstart","zoom","zoomend","move","moveend","resize","render","idle"\]/,
    "MapLibre zoom, pan and resize events are included in performance diagnostics");
});

test("simulation and legacy pages contain no terrain elevation feature or module", () => {
  const legacyPages = ["bakup.html", "fff.html"].map(name =>
    fs.readFileSync(path.join(__dirname, "..", name), "utf8"));
  for (const [name, source] of [["simulation page", page], ...legacyPages.map((source, index) =>
    [["backup page", "alternate legacy page"][index], source])]) {
    assert.doesNotMatch(source, /terrain[_ -]?(elevation|controller|status|threshold|panel|scale)|TerrainElevation|data-layer="terrain"/i,
      `${name} has no terrain elevation UI, loader, or controller`);
  }
  assert.equal(fs.existsSync(path.join(__dirname, "..", "app", "static", "js", "terrain_elevation.js")), false,
    "terrain renderer module is removed");
});
