/**
 * Country-centroid computation for dashboard geographic maps.
 *
 * Derives centroids from GeoJSON polygon geometry (d3 world-atlas 50m)
 * rather than hardcoded coordinates. Selects the largest polygon per
 * feature to anchor island nations to their primary landmass. Unwraps
 * antimeridian-crossing features (e.g. Russia, Fiji) before averaging
 * to prevent centroid collapse to longitude zero.
 *
 * Geometry extraction swallows malformed rings defensively to ensure
 * a corrupted feature cannot invalidate the global lookup map.
 */

export type Lnglat = [number, number];

export interface Centroid {
  // Canonical display name from atlas properties, used for tooltips.
  name: string;
  coords: Lnglat;
}

/**
 * Maps entity extraction variants, abbreviations, and ingest typos to
 * exact canonical keys matching the world-atlas 50m feature set.
 */
export const COUNTRY_ALIASES: Record<string, string> = {
  // United States
  usa: 'United States of America',
  'u.s.a.': 'United States of America',
  'u.s.': 'United States of America',
  us: 'United States of America',
  'united states': 'United States of America',
  america: 'United States of America',
  // United Kingdom
  uk: 'United Kingdom',
  'u.k.': 'United Kingdom',
  britain: 'United Kingdom',
  'great britain': 'United Kingdom',
  england: 'United Kingdom',
  // Russia / Turkey / Iran / Syria / Vietnam
  'russian federation': 'Russia',
  türkiye: 'Turkey',
  turkiye: 'Turkey',
  'iran (islamic republic of)': 'Iran',
  'syrian arab republic': 'Syria',
  'viet nam': 'Vietnam',
  // South America
  'venezuela (bolivarian republic of)': 'Venezuela',
  'venezuela bolivarian republic of': 'Venezuela',
  'bolivia (plurinational state of)': 'Bolivia',
  // Africa
  'united republic of tanzania': 'Tanzania',
  morrocco: 'Morocco',
  'burkina-faso': 'Burkina Faso',
  // Eastern Europe
  'republic of moldova': 'Moldova',
  'north macedonia': 'Macedonia',
  macedoni: 'Macedonia', // ingest typo
  hrvatska: 'Croatia',
  deutschland: 'Germany',
  slovensko: 'Slovakia',
  bosnia: 'Bosnia and Herz.',
  kosova: 'Kosovo',
  // Korea (world-atlas keeps them split — default bare "Korea" to South)
  korea: 'South Korea',
  'south korea': 'South Korea',
  'republic of korea': 'South Korea',
  'north korea': 'North Korea',
  "korea, democratic people's republic of": 'North Korea',
  // Czechia / Laos
  'czech republic': 'Czechia',
  czechia: 'Czechia',
  laos: 'Laos',
  "lao people's democratic republic": 'Laos',
  // DRC
  drc: 'Dem. Rep. Congo',
  'democratic republic of congo': 'Dem. Rep. Congo',
  'democratic republic of the congo': 'Dem. Rep. Congo',
  // Ivory Coast
  'ivory coast': "Côte d'Ivoire",
  "cote d'ivoire": "Côte d'Ivoire",
  'cote d ivoire': "Côte d'Ivoire",
  // Asia
  'taiwan province of china': 'Taiwan',
  // Caribbean
  'dominican republic': 'Dominican Rep.',
  'antigua and barbuda': 'Antigua and Barb.',
  curacao: 'Curaçao',
  // Oceania
  'french polynesia': 'Fr. Polynesia',
  // Cities → owning country
  havana: 'Cuba',
  // São Tomé encoding artifacts (50m uses "Principe" without acute)
  's. tom� and pr�ncipe': 'São Tomé and Principe',
  's. tom?� and pr??ncipe': 'São Tomé and Principe',
  'sao tome and principe': 'São Tomé and Principe',
  'sao tome': 'São Tomé and Principe',
  'são tomé and príncipe': 'São Tomé and Principe',
};

function isLnglatPoint(p: unknown): p is Lnglat {
  return (
    Array.isArray(p) &&
    p.length >= 2 &&
    typeof p[0] === 'number' &&
    Number.isFinite(p[0]) &&
    typeof p[1] === 'number' &&
    Number.isFinite(p[1])
  );
}

function isRing(r: unknown): r is Lnglat[] {
  return Array.isArray(r) && r.length > 0 && r.every(isLnglatPoint);
}

function ringArea(ring: Lnglat[]): number {
  if (ring.length < 3) return 0;
  let a = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  }
  return Math.abs(a / 2);
}

function ringBboxCenter(ring: Lnglat[]): Lnglat | null {
  let xMin = Infinity,
    yMin = Infinity,
    xMax = -Infinity,
    yMax = -Infinity;
  for (const [x, y] of ring) {
    if (x < xMin) xMin = x;
    if (x > xMax) xMax = x;
    if (y < yMin) yMin = y;
    if (y > yMax) yMax = y;
  }
  if (!Number.isFinite(xMin) || !Number.isFinite(yMin)) return null;

  let cx: number;
  if (xMax - xMin > 270) {
    // Unwraps coordinates crossing the antimeridian (+360 shift) so
    // features spanning +/-180 average correctly across the date line.
    let sum = 0;
    for (const [x] of ring) sum += x < 0 ? x + 360 : x;
    cx = sum / ring.length;
    if (cx > 180) cx -= 360;
  } else {
    cx = (xMin + xMax) / 2;
  }
  const cy = (yMin + yMax) / 2;
  return Number.isFinite(cx) && Number.isFinite(cy) ? [cx, cy] : null;
}

// Traverses GeoJSON geometry trees to extract outer boundary rings.
function collectOuterRings(geom: unknown): Lnglat[][] {
  const rings: Lnglat[][] = [];
  if (!geom || typeof geom !== 'object') return rings;
  const g = geom as {
    type?: string;
    coordinates?: unknown;
    geometries?: unknown[];
  };
  if (g.type === 'Polygon') {
    const ring = (g.coordinates as unknown[] | undefined)?.[0];
    if (isRing(ring)) rings.push(ring);
  } else if (g.type === 'MultiPolygon') {
    for (const poly of (g.coordinates as unknown[] | undefined) ?? []) {
      const ring = (poly as unknown[] | undefined)?.[0];
      if (isRing(ring)) rings.push(ring);
    }
  } else if (g.type === 'GeometryCollection') {
    for (const sub of g.geometries ?? []) {
      rings.push(...collectOuterRings(sub));
    }
  }
  return rings;
}

function featureCentroid(feature: unknown): Lnglat | null {
  const f = feature as { geometry?: unknown };
  const rings = collectOuterRings(f?.geometry);
  if (rings.length === 0) return null;
  // Anchors multi-polygon features to their primary landmass by area.
  let largest = rings[0];
  let maxArea = ringArea(largest);
  for (let i = 1; i < rings.length; i++) {
    const a = ringArea(rings[i]);
    if (a > maxArea) {
      maxArea = a;
      largest = rings[i];
    }
  }
  return ringBboxCenter(largest);
}

export function buildCentroidMap(world: unknown): Map<string, Centroid> {
  const map = new Map<string, Centroid>();
  const features = (world as { features?: unknown[] } | null)?.features ?? [];
  if (Array.isArray(features)) {
    let failed = 0;
    for (const f of features) {
      try {
        const name = (f as { properties?: { name?: unknown } } | null)
          ?.properties?.name;
        if (typeof name !== 'string' || !name.trim()) continue;
        const coords = featureCentroid(f);
        if (coords) map.set(name.toLowerCase(), { name, coords });
      } catch {
        failed += 1;
        // Skip malformed features to preserve the remaining lookup map.
      }
    }
    if (failed > 0) {
      console.warn(`[mapCentroids] skipped ${failed} malformed feature(s)`);
    }
  }
  return map;
}

export function lookupCentroid(
  rawName: string,
  centroids: Map<string, Centroid>,
): Centroid | null {
  const key = rawName.trim().toLowerCase();
  const direct = centroids.get(key);
  if (direct) return direct;
  const aliased = COUNTRY_ALIASES[key];
  return aliased ? (centroids.get(aliased.toLowerCase()) ?? null) : null;
}

/**
 * Splits delimited multi-country strings.
 *
 * Uses case-sensitive ' And ' delimiter matching database formatting
 * to avoid erroneously splitting canonical names containing lowercase
 * 'and' (e.g. 'Bosnia and Herz.', 'Trinidad and Tobago').
 */
export function splitMultiCountry(rawName: string): string[] {
  const parts = rawName
    .split(/\s+And\s+|,\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  return parts.length > 1 ? parts : [rawName];
}
