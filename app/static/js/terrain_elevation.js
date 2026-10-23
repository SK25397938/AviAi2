(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.TerrainElevation = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const REQUIRED = ["lat", "lon", "elevation_m", "elevation_ft", "distance_nm", "bearing_deg"];
  const THRESHOLDS = [300, 500, 750, 1000];
  const CONTOUR_INTERVAL_M = 100;
  const SOURCE_ID = "terrain-elevation-contours";
  const LAYER_ID = "terrain-elevation-contour-lines";
  const DEFAULT_CENTER = {lat: 19.0896, lon: 72.8656};
  const EARTH_RADIUS_NM = 3440.065;

  function distanceNm(a, b) {
    const rad = Math.PI / 180;
    const dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * EARTH_RADIUS_NM * Math.asin(Math.sqrt(Math.min(1, h)));
  }

  function parseCsv(text, center = DEFAULT_CENTER) {
    const lines = String(text || "").trim().split(/\r?\n/);
    if (lines.length < 2) throw new Error("Terrain elevation CSV is empty.");
    const header = lines[0].split(",").map(x => x.trim());
    const indices = Object.fromEntries(REQUIRED.map(k => [k, header.indexOf(k)]));
    const absent = REQUIRED.filter(k => indices[k] < 0);
    if (absent.length) throw new Error("Terrain elevation CSV is missing columns: " + absent.join(", "));
    const points = [];
    let invalid = 0, maxDistanceErrorNm = 0;
    for (let i = 1; i < lines.length; i++) {
      if (!lines[i].trim()) continue;
      const cells = lines[i].split(",");
      const vals = Object.fromEntries(REQUIRED.map(k => [k, Number(cells[indices[k]])]));
      if (REQUIRED.some(k => cells[indices[k]] == null || cells[indices[k]].trim() === "" || !Number.isFinite(vals[k])) ||
          vals.lat < -90 || vals.lat > 90 || vals.lon < -180 || vals.lon > 180 || vals.distance_nm < 0) {
        invalid++;
        continue;
      }
      const p = {lat: vals.lat, lon: vals.lon, elevation_m: vals.elevation_m,
        elevation_ft: vals.elevation_ft, distance_nm: vals.distance_nm, bearing_deg: vals.bearing_deg};
      maxDistanceErrorNm = Math.max(maxDistanceErrorNm, Math.abs(distanceNm(center, p) - p.distance_nm));
      points.push(p);
    }
    if (!points.length) throw new Error("Terrain elevation CSV contains no valid samples.");
    if (maxDistanceErrorNm > 1) throw new Error("Terrain distance_nm values disagree with the VABB reference by more than 1 NM.");
    const samples = points.filter(p => p.distance_nm <= 60);
    if (!samples.length) throw new Error("No terrain samples fall within 60 NM of VABB.");
    return {points, samples, invalid, maxDistanceErrorNm,
      min_m: Math.min(...samples.map(p => p.elevation_m)), max_m: Math.max(...samples.map(p => p.elevation_m))};
  }

  function median(values) {
    if (!values.length) return 0;
    const sorted = values.slice().sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function nominalStep(values) {
    const counts = new Map();
    for (let i = 1; i < values.length; i++) {
      const step = values[i] - values[i - 1];
      if (step > 0) {
        const key = step.toFixed(6);
        counts.set(key, (counts.get(key) || 0) + 1);
      }
    }
    const mode = [...counts.entries()].sort((a, b) => b[1] - a[1] || Number(a[0]) - Number(b[0]))[0];
    return mode ? Number(mode[0]) : 0;
  }

  function coordKey(p) { return p.lat.toFixed(7) + "," + p.lon.toFixed(7); }
  function pointKey(p) { return p[0].toFixed(6) + "," + p[1].toFixed(6); }

  function interpolateEdge(a, b, level) {
    const delta = b.elevation_m - a.elevation_m;
    const t = delta === 0 ? 0.5 : Math.max(0, Math.min(1, (level - a.elevation_m) / delta));
    return [a.lon + (b.lon - a.lon) * t, a.lat + (b.lat - a.lat) * t];
  }

  function clippedSegment(a, b, center, radiusNm) {
    const at = t => ({lat: a[1] + (b[1] - a[1]) * t, lon: a[0] + (b[0] - a[0]) * t});
    const inside = t => distanceNm(center, at(t)) <= radiusNm;
    if (inside(0) && inside(1)) return [a, b];
    // Sample only boundary-crossing cells; their length is about half a nautical mile.
    const divisions = 24, hits = [];
    let prevT = 0, prevInside = inside(0);
    if (prevInside) hits.push(0);
    for (let i = 1; i <= divisions; i++) {
      const t = i / divisions, isInside = inside(t);
      if (isInside !== prevInside) {
        let lo = prevT, hi = t, loInside = prevInside;
        for (let j = 0; j < 24; j++) {
          const mid = (lo + hi) / 2;
          if (inside(mid) === loInside) lo = mid; else hi = mid;
        }
        hits.push((lo + hi) / 2);
      }
      if (isInside) hits.push(t);
      prevT = t; prevInside = isInside;
    }
    if (!hits.length) return null;
    const from = Math.min(...hits), to = Math.max(...hits);
    if (to - from < 1e-10) return null;
    return [[at(from).lon, at(from).lat], [at(to).lon, at(to).lat]];
  }

  function stitchSegments(segments, level) {
    const adjacency = new Map();
    segments.forEach((segment, index) => segment.forEach((point, end) => {
      const key = pointKey(point);
      if (!adjacency.has(key)) adjacency.set(key, []);
      adjacency.get(key).push({index, end});
    }));
    const used = new Uint8Array(segments.length), lines = [];
    function trace(startIndex, startEnd) {
      const line = [], first = segments[startIndex][startEnd];
      let index = startIndex, end = startEnd, current = first;
      line.push(first);
      while (!used[index]) {
        used[index] = 1;
        const nextEnd = 1 - end, next = segments[index][nextEnd];
        line.push(next);
        const links = adjacency.get(pointKey(next)) || [];
        const candidate = links.find(link => !used[link.index]);
        if (!candidate) break;
        index = candidate.index; end = candidate.end; current = next;
      }
      if (line.length > 1) lines.push({type: "Feature", properties: {elevation_m: level},
        geometry: {type: "LineString", coordinates: line}});
    }
    for (let i = 0; i < segments.length; i++) {
      if (used[i]) continue;
      const aLinks = adjacency.get(pointKey(segments[i][0])) || [];
      const bLinks = adjacency.get(pointKey(segments[i][1])) || [];
      if (aLinks.length !== 2) trace(i, 0);
      else if (bLinks.length !== 2) trace(i, 1);
    }
    for (let i = 0; i < segments.length; i++) if (!used[i]) trace(i, 0);
    return lines;
  }

  function contourLevels(points, thresholdM, intervalM = CONTOUR_INTERVAL_M) {
    const threshold = Number(thresholdM), interval = Number(intervalM);
    if (!THRESHOLDS.includes(threshold)) throw new Error("Unsupported terrain contour threshold.");
    if (!(interval > 0) || !Number.isFinite(interval)) throw new Error("Contour interval must be a positive number of metres.");
    let max = -Infinity;
    for (const p of points) if (Number.isFinite(p.elevation_m)) max = Math.max(max, p.elevation_m);
    const first = Math.ceil(threshold / interval) * interval, levels = [];
    for (let level = first; level < max; level += interval) levels.push(level);
    return levels;
  }

  function generateContours(points, thresholdM = 500, options = {}) {
    const center = options.center || DEFAULT_CENTER;
    const radiusNm = options.radiusNm == null ? 60 : options.radiusNm;
    const intervalM = options.intervalM == null ? CONTOUR_INTERVAL_M : options.intervalM;
    const valid = points.filter(p => Number.isFinite(p.lat) && Number.isFinite(p.lon) &&
      Number.isFinite(p.elevation_m) && Number.isFinite(p.distance_nm) && p.distance_nm >= 0);
    if (!valid.length) return {type: "FeatureCollection", features: []};
    const lats = [...new Set(valid.map(p => p.lat))].sort((a, b) => a - b);
    const lons = [...new Set(valid.map(p => p.lon))].sort((a, b) => a - b);
    const latStep = nominalStep(lats), lonStep = nominalStep(lons);
    if (!(latStep > 0) || !(lonStep > 0)) return {type: "FeatureCollection", features: []};
    const grid = new Map(valid.map(p => [coordKey(p), p]));
    const levels = contourLevels(valid, thresholdM, intervalM), features = [];
    for (let y = 0; y < lats.length - 1; y++) {
      if (lats[y + 1] - lats[y] > latStep * 1.5) continue;
      for (let x = 0; x < lons.length - 1; x++) {
        if (lons[x + 1] - lons[x] > lonStep * 1.5) continue;
        const p0 = grid.get(coordKey({lat: lats[y + 1], lon: lons[x]})); // NW
        const p1 = grid.get(coordKey({lat: lats[y + 1], lon: lons[x + 1]})); // NE
        const p2 = grid.get(coordKey({lat: lats[y], lon: lons[x + 1]})); // SE
        const p3 = grid.get(coordKey({lat: lats[y], lon: lons[x]})); // SW
        if (!p0 || !p1 || !p2 || !p3) continue; // no contour across nodata cells
        const cellDiagonalNm = distanceNm(p0, p2);
        if (Math.min(p0.distance_nm, p1.distance_nm, p2.distance_nm, p3.distance_nm) > radiusNm + cellDiagonalNm) continue;
        const corners = [p0, p1, p2, p3], edges = [[0, 1], [1, 2], [2, 3], [3, 0]];
        for (const level of levels) {
          if (corners.every(p => p.elevation_m < level) || corners.every(p => p.elevation_m >= level)) continue;
          const crossings = [];
          for (let edge = 0; edge < 4; edge++) {
            const [a, b] = edges[edge], va = corners[a].elevation_m, vb = corners[b].elevation_m;
            if ((va < level && vb >= level) || (vb < level && va >= level)) crossings.push({edge, point: interpolateEdge(corners[a], corners[b], level)});
          }
          let pairs = [];
          if (crossings.length === 2) pairs = [[crossings[0], crossings[1]]];
          else if (crossings.length === 4) {
            const centerHigh = corners.reduce((sum, p) => sum + p.elevation_m, 0) / 4 >= level;
            const byEdge = new Map(crossings.map(item => [item.edge, item]));
            pairs = centerHigh ? [[byEdge.get(0), byEdge.get(1)], [byEdge.get(2), byEdge.get(3)]] :
              [[byEdge.get(0), byEdge.get(3)], [byEdge.get(1), byEdge.get(2)]];
          }
          for (const [a, b] of pairs) {
            const clipped = clippedSegment(a.point, b.point, center, radiusNm);
            if (clipped) features.push({level, segment: clipped});
          }
        }
      }
    }
    const grouped = new Map();
    for (const item of features) {
      if (!grouped.has(item.level)) grouped.set(item.level, []);
      grouped.get(item.level).push(item.segment);
    }
    const result = [];
    for (const [level, segments] of grouped) result.push(...stitchSegments(segments, level));
    return {type: "FeatureCollection", features: result};
  }

  function createController(map, getEnabled, setStatus) {
    let parsed = null, loading = null, thresholdM = 500;
    const cache = new Map();
    const enabled = () => Boolean(getEnabled());
    function geometry() {
      if (!cache.has(thresholdM)) cache.set(thresholdM, generateContours(parsed.points, thresholdM));
      return cache.get(thresholdM);
    }
    function removeTerrain() {
      if (map.getLayer(LAYER_ID)) map.removeLayer(LAYER_ID);
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
    }
    function belowLayer() {
      const layers = map.getStyle && map.getStyle() && map.getStyle().layers;
      const firstSymbol = layers && layers.find(layer => layer.type === "symbol");
      if (firstSymbol) return firstSymbol.id;
      if (map.getLayer("selected-edge")) return "selected-edge";
      if (map.getLayer("guidance-lines")) return "guidance-lines";
      return map.getLayer("runways-line") ? "runways-line" : undefined;
    }
    function updateSource() {
      if (!parsed) return;
      const source = map.getSource(SOURCE_ID);
      if (source && source.setData) source.setData(geometry());
    }
    async function ensure() {
      if (!enabled() || !map.isStyleLoaded()) return;
      if (!parsed) {
        if (!loading) loading = fetch("/static/data/vabb_80nm_elevation_grid.csv")
          .then(r => { if (!r.ok) throw new Error("Terrain data request failed (" + r.status + ")."); return r.text(); })
          .then(text => { parsed = parseCsv(text); return parsed; })
          .catch(error => { loading = null; throw error; });
        try { await loading; }
        catch (e) { setStatus("Terrain elevation unavailable: " + e.message, true); return; }
      }
      if (!enabled() || !map.isStyleLoaded()) return;
      try {
        const data = geometry();
        if (!map.getSource(SOURCE_ID)) map.addSource(SOURCE_ID, {type: "geojson", data});
        if (!map.getLayer(LAYER_ID)) {
          const layer = {id: LAYER_ID, type: "line", source: SOURCE_ID,
            paint: {"line-color": "#c6a77a", "line-width": 1.05, "line-opacity": 0.52, "line-blur": 0.15},
            layout: {"line-cap": "round", "line-join": "round"}};
          const before = belowLayer();
          if (before) map.addLayer(layer, before); else map.addLayer(layer);
        }
        setStatus("100 m contours from " + thresholdM + " m - " + data.features.length.toLocaleString() + " line segments", false);
      } catch (e) { setStatus("Terrain elevation unavailable: " + e.message, true); }
    }
    const controller = {
      setVisible(value) { if (!value) { removeTerrain(); return; } return ensure(); },
      styleLoaded() { return ensure(); },
      setThreshold(value) {
        const next = Number(value);
        if (!THRESHOLDS.includes(next)) throw new Error("Unsupported terrain contour threshold.");
        thresholdM = next;
        if (!enabled() || !parsed) return;
        updateSource();
        setStatus("100 m contours from " + thresholdM + " m - " + geometry().features.length.toLocaleString() + " line segments", false);
      },
      removeTerrain,
      getThreshold() { return thresholdM; }
    };
    return controller;
  }

  return {parseCsv, generateContours, contourLevels, createController, THRESHOLDS,
    CONTOUR_INTERVAL_M, DEFAULT_CENTER, SOURCE_ID, LAYER_ID};
});
