export interface EntityColorEntry {
  hex: string;
  tailwind: string;
}

const ENTITY_COLORS: Record<string, EntityColorEntry> = {
  chemical: { hex: '#2563EB', tailwind: 'blue-600' },
  species: { hex: '#16A34A', tailwind: 'green-600' },
  plant_part: { hex: '#84CC16', tailwind: 'lime-500' },
  extraction_method: { hex: '#7C3AED', tailwind: 'violet-600' },
  development_stage: { hex: '#F97316', tailwind: 'orange-500' },
  season: { hex: '#EAB308', tailwind: 'yellow-500' },
  location: { hex: '#06B6D4', tailwind: 'cyan-500' },
  bioactivity: { hex: '#EC4899', tailwind: 'pink-500' },
  analytical_technique: { hex: '#64748B', tailwind: 'slate-500' },
  isolation_method: { hex: '#8B5CF6', tailwind: 'violet-500' },
  disease: { hex: '#DC2626', tailwind: 'red-600' },
  default: { hex: '#6B7280', tailwind: 'gray-500' },
};

/**
 * Maps entity labels to palette entries, defaulting to neutral gray
 * for unrecognized categories.
 */
export function getEntityColor(label: string): EntityColorEntry {
  const key = label.toLowerCase().replace(/\s+/g, '_');
  return ENTITY_COLORS[key] ?? ENTITY_COLORS.default;
}

/**
 * Human-readable labels for entity categories across UI controls.
 */
export const ENTITY_DISPLAY_NAMES: Record<string, string> = {
  chemical: 'Chemical',
  species: 'Species',
  plant_part: 'Plant part',
  extraction_method: 'Extraction method',
  development_stage: 'Development stage',
  season: 'Season',
  location: 'Location',
  bioactivity: 'Bioactivity',
  analytical_technique: 'Analytical technique',
  isolation_method: 'Isolation method',
  disease: 'Disease',
};

/**
 * Normalized 2D coordinates for semantic force-directed clustering:
 * Source (left) -> Process (center) -> Outcome (right).
 */
export const ENTITY_CLUSTER_POSITIONS: Record<
  string,
  { x: number; y: number }
> = {
  // Source entities
  chemical: { x: 0.12, y: 0.5 },
  species: { x: 0.22, y: 0.5 },
  // Botanical anatomy
  plant_part: { x: 0.4, y: 0.5 },
  // Experimental pipeline
  extraction_method: { x: 0.58, y: 0.5 },
  analytical_technique: { x: 0.5, y: 0.38 },
  isolation_method: { x: 0.5, y: 0.62 },
  // Biological effects
  bioactivity: { x: 0.78, y: 0.38 },
  disease: { x: 0.82, y: 0.62 },
  // Environmental metadata
  location: { x: 0.35, y: 0.22 },
  season: { x: 0.35, y: 0.78 },
  development_stage: { x: 0.22, y: 0.72 },
};

export const ENTITY_CATEGORY_INDEX: Record<string, number> = {
  chemical: 0,
  species: 1,
  plant_part: 2,
  extraction_method: 3,
  analytical_technique: 4,
  isolation_method: 5,
  bioactivity: 6,
  disease: 7,
  location: 8,
  season: 9,
  development_stage: 10,
};

/**
 * Generates an ECharts series item style matching the entity color.
 */
export function getEchartsItemStyle(label: string) {
  const color = getEntityColor(label);
  return {
    color: color.hex,
  };
}
