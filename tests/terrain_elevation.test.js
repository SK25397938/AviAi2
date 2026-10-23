const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const terrain = require("../app/static/js/terrain_elevation.js");

const header = "lat,lon,elevation_m,elevation_ft,distance_nm,bearing_deg";
function row(lat, lon, elevationM, distanceNm = 40, elevationFt = 0) {
  return `${lat},${lon},${elevationM},${elevationFt},${distanceNm},0`;
}
function grid(points) { return [header, ...points.map(p => row(...p))].join("\n"); }
function cell(center = {lat: 0.5, lon: 0.5}, high = 650, low = 0, radius = 0.01) {
  return [
    {lat: center.lat + radius, lon: center.lon - radius, elevation_m: high, elevation_ft: 0, distance_nm: 40},
    {lat: center.lat + radius, lon: center.lon + radius, elevation_m: high, elevation_ft: 0, distance_nm: 40},
    {lat: center.lat - radius, lon: center.lon + radius, elevation_m: low, elevation_ft: 0, distance_nm: 40},
    {lat: center.lat - radius, lon: center.lon - radius, elevation_m: low, elevation_ft: 0, distance_nm: 40}
  ];
}

test("marching squares creates equal-elevation isolines with lon/lat GeoJSON coordinates", () => {
  const center = {lat: 0.5, lon: 0.5};
  const fc = terrain.generateContours(cell(center), 500, {center});
  assert.deepEqual(fc.features.map(f => f.properties.elevation_m), [500, 600]);
  const f = fc.features.find(feature => feature.properties.elevation_m === 500);
  assert.ok(f);
  assert.equal(f.geometry.type, "LineString");
  assert.ok(f.geometry.coordinates.every(([lon, lat]) => lon >= 0.49 && lon <= 0.51 && lat > 0.5));
});

test("thresholds use elevation_m, round upward to 100 m contour levels, and never emit lower levels", () => {
  const points = cell(undefined, 950, 0);
  const fc = terrain.generateContours(points, 750, {center: {lat: 0.5, lon: 0.5}});
  assert.deepEqual([...new Set(fc.features.map(f => f.properties.elevation_m))], [800, 900]);
  assert.ok(fc.features.every(f => f.properties.elevation_m >= 750));
  assert.equal(terrain.generateContours(points, 1000, {center: {lat: 0.5, lon: 0.5}}).features.length, 0);
  assert.deepEqual(terrain.contourLevels(points, 750), [800, 900]);
});

test("contour geometry is clipped to the 60 NM great-circle boundary", () => {
  const center = {lat: 0, lon: 0};
  const points = [
    {lat: 0.02, lon: -1.1, elevation_m: 650, distance_nm: 70},
    {lat: 0.02, lon: 1.1, elevation_m: 650, distance_nm: 70},
    {lat: -0.02, lon: 1.1, elevation_m: 0, distance_nm: 70},
    {lat: -0.02, lon: -1.1, elevation_m: 0, distance_nm: 70}
  ];
  const fc = terrain.generateContours(points, 500, {center, radiusNm: 60});
  assert.deepEqual(fc.features.map(f => f.properties.elevation_m), [500, 600]);
  const coords = fc.features[0].geometry.coordinates;
  assert.ok(coords.length >= 2);
  for (const [lon, lat] of coords) {
    const d = 2 * 3440.065 * Math.asin(Math.sqrt(Math.sin(lat * Math.PI / 360) ** 2 +
      Math.cos(0) * Math.cos(lat * Math.PI / 180) * Math.sin(lon * Math.PI / 360) ** 2));
    assert.ok(d <= 60.00001, `point lies ${d} NM from the center`);
  }
});

test("missing samples and irregular grid gaps do not get bridged", () => {
  const center = {lat: 0.5, lon: 0.5};
  const missing = cell(center).slice(0, 3);
  assert.equal(terrain.generateContours(missing, 500, {center}).features.length, 0);
  const irregular = [
    {lat: 1, lon: 0, elevation_m: 650, distance_nm: 40}, {lat: 1, lon: 1, elevation_m: 650, distance_nm: 40},
    {lat: 0, lon: 0, elevation_m: 0, distance_nm: 40}, {lat: 0, lon: 1, elevation_m: 0, distance_nm: 40},
    {lat: 1, lon: 3, elevation_m: 650, distance_nm: 40}, {lat: 0, lon: 3, elevation_m: 0, distance_nm: 40}
  ];
  const fc = terrain.generateContours(irregular, 500, {center});
  assert.deepEqual(fc.features.map(f => f.properties.elevation_m), [500, 600]);
  assert.ok(fc.features.every(f => f.geometry.coordinates.every(([lon]) => lon <= 1)));
});

test("CSV parser uses metre field and rejects missing or invalid values", () => {
  const center = {lat: 19.0896, lon: 72.8656};
  const csv = [header,
    row(center.lat, center.lon, 0, 0, 25000), // valid sea-level value; feet are intentionally unrelated
    `${center.lat},${center.lon + 0.01},,0,0.5,90`,
    "bad,72.8,600,1968,1,0"].join("\n");
  const parsed = terrain.parseCsv(csv, center);
  assert.equal(parsed.samples.length, 1);
  assert.equal(parsed.samples[0].elevation_m, 0);
  assert.equal(parsed.invalid, 2);
  assert.throws(() => terrain.parseCsv("lat,lon\n1,2", center), /missing columns/);
});

test("threshold changes update displayed geometry; toggles and style reloads do not duplicate layers or touch others", async () => {
  const sources = new Map(), layers = new Set(["aircraft-layer", "route-layer"]);
  let addSources = 0, addLayers = 0;
  const map = {
    isStyleLoaded: () => true,
    getStyle: () => ({layers: [{id: "road-labels", type: "symbol"}]}),
    getSource: id => sources.get(id),
    addSource: (id, config) => { addSources++; const source = {data: config.data, setData(data) { this.data = data; }}; sources.set(id, source); },
    removeSource: id => sources.delete(id),
    getLayer: id => layers.has(id) ? {id} : null,
    addLayer: layer => { addLayers++; layers.add(layer.id); },
    removeLayer: id => layers.delete(id)
  };
  const c = {lat: 19.0896, lon: 72.8656};
  const csv = grid([
    [c.lat + 0.01, c.lon - 0.01, 950, 0, 0], [c.lat + 0.01, c.lon + 0.01, 950, 0, 0],
    [c.lat - 0.01, c.lon + 0.01, 0, 0, 0], [c.lat - 0.01, c.lon - 0.01, 0, 0, 0]
  ]);
  const oldFetch = global.fetch;
  global.fetch = async () => ({ok: true, text: async () => csv});
  try {
    let visible = false;
    const controller = terrain.createController(map, () => visible, () => {});
    visible = true; await controller.setVisible(true);
    const source = map.getSource(terrain.SOURCE_ID);
    const at500 = source.data.features.map(f => f.properties.elevation_m);
    controller.setThreshold(750);
    const at750 = source.data.features.map(f => f.properties.elevation_m);
    assert.notDeepEqual(at750, at500);
    assert.ok(at750.every(level => level >= 750));
    assert.equal(layers.has("route-layer"), true);
    assert.equal(map.getStyle().layers[0].id, "road-labels");
    // Repeated ensure is idempotent; a replaced style requires one source/layer reconstruction.
    await controller.styleLoaded();
    assert.equal(addSources, 1); assert.equal(addLayers, 1);
    sources.clear(); layers.delete(terrain.LAYER_ID);
    await controller.styleLoaded();
    assert.equal(addSources, 2); assert.equal(addLayers, 2);
    await controller.styleLoaded();
    assert.equal(addSources, 2); assert.equal(addLayers, 2);
    visible = false; controller.setVisible(false);
    assert.equal(sources.has(terrain.SOURCE_ID), false);
    assert.equal(layers.has(terrain.LAYER_ID), false);
    assert.equal(layers.has("aircraft-layer"), true);
    assert.equal(layers.has("route-layer"), true);
  } finally { global.fetch = oldFetch; }
});

test("supplied grid contour generation stays performant and clips all output", () => {
  const csvPath = path.join(__dirname, "..", "app", "static", "data", "vabb_80nm_elevation_grid.csv");
  const parsed = terrain.parseCsv(fs.readFileSync(csvPath, "utf8"));
  assert.equal(parsed.points.length, 61803);
  assert.equal(parsed.samples.length, 39240);
  const start = process.hrtime.bigint();
  const fc = terrain.generateContours(parsed.points, 500);
  const elapsedMs = Number(process.hrtime.bigint() - start) / 1e6;
  assert.ok(fc.features.length > 0);
  assert.ok(elapsedMs < 10000, `generation took ${elapsedMs.toFixed(1)} ms`);
  for (const feature of fc.features) for (const [lon, lat] of feature.geometry.coordinates) {
    const d = 2 * 3440.065 * Math.asin(Math.sqrt(Math.sin((lat - terrain.DEFAULT_CENTER.lat) * Math.PI / 360) ** 2 +
      Math.cos(terrain.DEFAULT_CENTER.lat * Math.PI / 180) * Math.cos(lat * Math.PI / 180) *
      Math.sin((lon - terrain.DEFAULT_CENTER.lon) * Math.PI / 360) ** 2));
    assert.ok(d <= 60.00001);
  }
});
