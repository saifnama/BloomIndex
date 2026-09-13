/**
 * Pure domain model for entity grouping, categorization, and popups.
 *
 * Encapsulates entity group taxonomies, CSS selector mappings, and
 * viewport-bounded positioning calculations for species and chemicals.
 */
import type { Entity } from '../../types';

export const SPECIES_SELECTOR = '.ent-species, mark.ner-species';
export const CHEMICAL_SELECTOR = '.ent-chemical, mark.ner-chemical';
export const SPECIES_POPUP_WIDTH = 320;
export const SPECIES_POPUP_ESTIMATED_HEIGHT = 160;

export const ENTITY_GROUP_ORDER = [
  'CHEMICAL',
  'SPECIES',
  'PLANT PART',
  'DEVELOPMENT STAGE',
  'EXTRACTION METHOD',
  'ANALYTICAL TECHNIQUE',
  'BIOACTIVITY',
  'DISEASE',
  'SEASON',
  'LOCATION',
] as const;

export type EntityGroupLabel = (typeof ENTITY_GROUP_ORDER)[number];

export const ENTITY_GROUP_CONFIG: Record<
  EntityGroupLabel,
  {
    accentVar: string;
    highlightSelector: string;
  }
> = {
  CHEMICAL: {
    accentVar: '--entity-chemical',
    highlightSelector: '.ent-chemical, mark.ner-chemical',
  },
  SPECIES: {
    accentVar: '--entity-species',
    highlightSelector: '.ent-species, mark.ner-species',
  },
  'PLANT PART': {
    accentVar: '--entity-plant-part',
    highlightSelector: '.ent-plant-part, mark.ner-plant-part',
  },
  'DEVELOPMENT STAGE': {
    accentVar: '--entity-development-stage',
    highlightSelector: '.ent-development-stage, mark.ner-development-stage',
  },
  'EXTRACTION METHOD': {
    accentVar: '--entity-extraction-method',
    highlightSelector: '.ent-extraction-method, mark.ner-extraction-method',
  },
  'ANALYTICAL TECHNIQUE': {
    accentVar: '--entity-analytical-technique',
    highlightSelector:
      '.ent-analytical-technique, mark.ner-analytical-technique, .ent-isolation-method, mark.ner-isolation-method',
  },
  BIOACTIVITY: {
    accentVar: '--entity-bioactivity',
    highlightSelector: '.ent-bioactivity, mark.ner-bioactivity',
  },
  DISEASE: {
    accentVar: '--entity-disease',
    highlightSelector: '.ent-disease, mark.ner-disease',
  },
  SEASON: {
    accentVar: '--entity-season',
    highlightSelector: '.ent-season, mark.ner-season',
  },
  LOCATION: {
    accentVar: '--entity-location',
    highlightSelector: '.ent-location, mark.ner-location',
  },
};

export const createInitialExpandedGroups = () => {
  return ENTITY_GROUP_ORDER.reduce<Record<EntityGroupLabel, boolean>>(
    (acc, label) => {
      acc[label] = label === 'CHEMICAL' || label === 'SPECIES';
      return acc;
    },
    {} as Record<EntityGroupLabel, boolean>,
  );
};

export const createInitialEnabledHighlightGroups = () => {
  return ENTITY_GROUP_ORDER.reduce<Record<EntityGroupLabel, boolean>>(
    (acc, label) => {
      acc[label] = true;
      return acc;
    },
    {} as Record<EntityGroupLabel, boolean>,
  );
};

export const getEntityGroupToken = (label: EntityGroupLabel) =>
  label.toLowerCase().replace(/[^a-z0-9]+/g, '-');

export type SpeciesPopupData = {
  primaryName: string;
  acceptedScientificName?: string;
  scientificNameVerified?: string;
  commonName?: string;
  canonical?: string;
  sourceDb?: string;
  sourceUrl?: string;
  taxonId?: string;
  matchStatus?: string;
  reviewRequired?: string;
  nameType?: Entity['name_type'];
  metadataScore: number;
};

export type SpeciesPopupState = {
  species: SpeciesPopupData;
  anchorText: string;
  position: {
    top: number;
    left: number;
  };
};

export type ChemicalPopupData = {
  primaryName: string;
  preferredName?: string;
  synonyms?: string[];
  smiles?: string;
  inchikey?: string;
  molecularFormula?: string;
  sourceDb?: string;
  sourceUrl?: string;
};

export type ChemicalPopupState = {
  chemical: ChemicalPopupData;
  anchorText: string;
  position: {
    top: number;
    left: number;
  };
};

export const stripHtml = (value?: string | null) =>
  (value || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

export const normalizeLookupText = (value?: string | null) =>
  stripHtml(value).toLowerCase();

export const getSpeciesPrimaryName = (entity: Entity) => {
  const acceptedScientificName = stripHtml(entity.accepted_scientific_name);
  const verifiedScientificName = stripHtml(entity.scientific_name_verified);
  const canonicalName = stripHtml(entity.canonical);
  const commonName = stripHtml(entity.common_name);
  const cleanText = stripHtml(entity.text);

  if (acceptedScientificName) return acceptedScientificName;
  if (verifiedScientificName) return verifiedScientificName;
  if (entity.name_type === 'scientific' && canonicalName) return canonicalName;
  if (
    canonicalName &&
    normalizeLookupText(canonicalName) !== normalizeLookupText(commonName)
  ) {
    return canonicalName;
  }
  return canonicalName || cleanText;
};

export const getSpeciesMetadataScore = (entity: Entity) => {
  let score = 0;
  if (stripHtml(entity.accepted_scientific_name)) score += 32;
  if (stripHtml(entity.scientific_name_verified)) score += 16;
  if (entity.name_type === 'scientific') score += 8;
  if (stripHtml(entity.common_name)) score += 4;
  if (stripHtml(entity.canonical)) score += 2;
  if (stripHtml(entity.source_db)) score += 2;
  if (stripHtml(entity.source_url)) score += 2;
  if (stripHtml(entity.taxon_id)) score += 2;
  if (stripHtml(entity.match_status)) score += 1;
  if (stripHtml(entity.review_required)) score += 1;
  return score;
};

export const pickBetterSpeciesRepresentative = (
  current: Entity,
  candidate: Entity,
) => {
  const currentScore = getSpeciesMetadataScore(current);
  const candidateScore = getSpeciesMetadataScore(candidate);

  if (candidateScore > currentScore) return candidate;
  if (candidateScore < currentScore) return current;

  return stripHtml(candidate.text).length > stripHtml(current.text).length
    ? candidate
    : current;
};

export const getSpeciesAliasList = (entity: Entity) => {
  const aliasSet = new Set<string>();
  const addAlias = (value?: string | null) => {
    const cleaned = stripHtml(value);
    if (cleaned) aliasSet.add(cleaned);
  };

  addAlias(entity.text);
  addAlias(entity.accepted_scientific_name);
  addAlias(entity.scientific_name_verified);
  addAlias(entity.canonical);
  addAlias(entity.common_name);
  (entity.aliases || []).forEach((alias) => addAlias(alias));

  return Array.from(aliasSet);
};

export const buildSpeciesPopupData = (entity: Entity): SpeciesPopupData => {
  return {
    primaryName: getSpeciesPrimaryName(entity),
    acceptedScientificName:
      stripHtml(entity.accepted_scientific_name) || undefined,
    scientificNameVerified:
      stripHtml(entity.scientific_name_verified) || undefined,
    commonName: stripHtml(entity.common_name) || undefined,
    canonical: stripHtml(entity.canonical) || undefined,
    sourceDb: stripHtml(entity.source_db) || undefined,
    sourceUrl: stripHtml(entity.source_url) || undefined,
    taxonId: stripHtml(entity.taxon_id) || undefined,
    matchStatus: stripHtml(entity.match_status) || undefined,
    reviewRequired: stripHtml(entity.review_required) || undefined,
    nameType: entity.name_type ?? undefined,
    metadataScore: getSpeciesMetadataScore(entity),
  };
};

export const getAnchorRect = (
  anchor: HTMLElement,
  fallbackPoint?: { x: number; y: number },
): {
  top: number;
  bottom: number;
  left: number;
  right: number;
  width: number;
  height: number;
} => {
  if (anchor && typeof anchor.getBoundingClientRect === 'function') {
    const r = anchor.getBoundingClientRect();
    if (r.width > 0 || r.height > 0) {
      return r;
    }
    const clientRects = anchor.getClientRects();
    if (clientRects && clientRects.length > 0) {
      for (let i = 0; i < clientRects.length; i++) {
        const cr = clientRects[i];
        if (cr.width > 0 || cr.height > 0) {
          return cr;
        }
      }
    }
    if (anchor.parentElement) {
      const pr = anchor.parentElement.getBoundingClientRect();
      if (pr.width > 0 || pr.height > 0) {
        return pr;
      }
    }
  }
  if (fallbackPoint && fallbackPoint.x > 0 && fallbackPoint.y > 0) {
    return {
      top: fallbackPoint.y - 10,
      bottom: fallbackPoint.y + 10,
      left: fallbackPoint.x - 20,
      right: fallbackPoint.x + 20,
      width: 40,
      height: 20,
    };
  }
  return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 };
};

export const getSpeciesPopupPosition = (
  anchor: HTMLElement,
  fallbackPoint?: { x: number; y: number },
  previousPos?: { top: number; left: number },
): { top: number; left: number } => {
  const rect = getAnchorRect(anchor, fallbackPoint);
  const margin = 12;
  const popupWidth = SPECIES_POPUP_WIDTH;

  if (
    rect.width === 0 &&
    rect.height === 0 &&
    rect.top === 0 &&
    rect.left === 0
  ) {
    if (previousPos) return previousPos;
  }

  // Anchors below the highlighted entity by default.
  let top = rect.bottom + 8;

  // Flips above when lower viewport clearance is insufficient.
  const spaceBelow = window.innerHeight - rect.bottom - margin;
  if (spaceBelow < 140 && rect.top - margin > 180) {
    top = Math.max(margin, rect.top - SPECIES_POPUP_ESTIMATED_HEIGHT - 8);
  } else if (top > window.innerHeight - 80) {
    top = Math.max(margin, window.innerHeight - 80);
  }

  const centeredLeft = rect.left + rect.width / 2 - popupWidth / 2;
  const left = Math.max(
    margin,
    Math.min(centeredLeft, window.innerWidth - popupWidth - margin),
  );

  return { top, left };
};

export const getChemicalPopupPosition = (
  anchor: HTMLElement,
  fallbackPoint?: { x: number; y: number },
  previousPos?: { top: number; left: number },
): { top: number; left: number } => {
  const rect = getAnchorRect(anchor, fallbackPoint);
  const margin = 12;
  const popupWidth = 320;

  if (
    rect.width === 0 &&
    rect.height === 0 &&
    rect.top === 0 &&
    rect.left === 0
  ) {
    if (previousPos) return previousPos;
  }

  // Anchors below the highlighted entity by default.
  let top = rect.bottom + 8;

  // Flips above when lower viewport clearance is insufficient.
  const spaceBelow = window.innerHeight - rect.bottom - margin;
  if (spaceBelow < 160 && rect.top - margin > 300) {
    top = Math.max(margin, rect.top - 330);
  } else if (top > window.innerHeight - 80) {
    top = Math.max(margin, window.innerHeight - 80);
  }

  const centeredLeft = rect.left + rect.width / 2 - popupWidth / 2;
  const left = Math.max(
    margin,
    Math.min(centeredLeft, window.innerWidth - popupWidth - margin),
  );

  return { top, left };
};

export interface GroupedEntities {
  [label: string]: {
    text: string;
    count: number;
    aliases: string[];
    subtitle?: string;
    // Chemical metadata properties
    preferred_name?: string;
    inchikey?: string;
    smiles?: string;
    molecular_formula?: string;
    source_db?: string;
    source_url?: string;
  }[];
}

export const isChemicalLikeLabel = (label: string) => label === 'CHEMICAL';
