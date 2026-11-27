// SA-01 PR 2: the product page's specifications grid and colour swatches. Every row comes from a
// field the catalog record has; a field it doesn't have is left out, never filled in (hard rule 5).
// This file only words and formats what was sent: labels, units, "Yes"/"No", sentence case.
import type { Product } from "./shopApi";

export type SpecRow = { label: string; value: string };

type Format = { label: string; unit?: string; grouped?: boolean };

// Known attribute keys: a label and, for a number, its unit. Any other key is shown with its own
// name in sentence case.
const ATTRIBUTES: Record<string, Format> = {
  care: { label: "Care" },
  country_of_origin: { label: "Country of origin" },
  dimensions: { label: "Dimensions" },
  fill: { label: "Fill" },
  fill_power: { label: "Fill power" },
  fit_guidance: { label: "Fit guidance" },
  fleece_weight: { label: "Fleece weight" },
  gauge: { label: "Gauge" },
  gsm: { label: "Fabric weight", unit: "gsm" },
  hood: { label: "Hood" },
  inseam_in: { label: "Inseam", unit: "in" },
  insulation: { label: "Insulation" },
  material: { label: "Material" },
  membrane: { label: "Membrane" },
  packable: { label: "Packable" },
  pile: { label: "Pile" },
  pocket_depth_in: { label: "Pocket depth", unit: "in" },
  quick_dry: { label: "Quick dry" },
  seam_sealed: { label: "Seam sealed" },
  stretch: { label: "Stretch" },
  thread_count: { label: "Thread count" },
  upf: { label: "UPF" },
  warmth: { label: "Warmth" },
  washable: { label: "Washable" },
  water_protection: { label: "Water protection" },
  waterproof_rating_mm: { label: "Waterproof rating", unit: "mm", grouped: true },
  weight_g: { label: "Weight", unit: "g" },
};

const GENDER: Record<string, string> = { mens: "Men's", womens: "Women's", unisex: "Unisex" };

/** "true_to_size" -> "True to size"; text that already has capitals keeps them. */
export function sentence(text: string): string {
  const spaced = text.replaceAll("_", " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function value(raw: unknown, format: Format | undefined): string | null {
  if (typeof raw === "boolean") return raw ? "Yes" : "No";
  if (typeof raw === "number" && Number.isFinite(raw)) {
    const shown = format?.grouped ? raw.toLocaleString("en-US") : String(raw);
    return format?.unit ? `${shown} ${format.unit}` : shown;
  }
  if (typeof raw === "string" && raw.trim() !== "") return sentence(raw);
  if (Array.isArray(raw)) {
    const parts = raw.filter((part): part is string | number => typeof part === "string" || typeof part === "number");
    return parts.length > 0 ? parts.map((part) => sentence(String(part))).join(", ") : null;
  }
  return null; // objects, null and empty values are left out
}

/** The grid, in a fixed order: the record's own fields, then its attributes by key. */
export function specRows(product: Product): SpecRow[] {
  const rows: SpecRow[] = [
    { label: "Product ID", value: product.product_id },
    { label: "Brand", value: product.brand },
    { label: "Department", value: product.department },
  ];
  if (product.subcategory) rows.push({ label: "Type", value: sentence(product.subcategory) });
  if (product.gender) rows.push({ label: "Gender", value: GENDER[product.gender] ?? sentence(product.gender) });
  if (product.fits && product.fits.length > 0) rows.push({ label: "Fit", value: product.fits.map(sentence).join(", ") });
  for (const key of Object.keys(product.attributes).sort()) {
    const format = Object.hasOwn(ATTRIBUTES, key) ? ATTRIBUTES[key] : undefined;
    const shown = value(product.attributes[key], format);
    if (shown !== null) rows.push({ label: format?.label ?? sentence(key), value: shown });
  }
  return rows.filter((row) => row.value !== "");
}

// The swatch for each colour name the catalog uses. A colour not listed gets a chip with no swatch,
// never a guessed one.
const SWATCHES: Record<string, string> = {
  Black: "#1a1a1a",
  Burgundy: "#6d1f2f",
  Charcoal: "#3b3d40",
  Clay: "#b0715a",
  Cobalt: "#1f4fa8",
  Forest: "#2f4a32",
  "Glacier Blue": "#a9c6d8",
  "Heather Grey": "#a3a3a3",
  Ivory: "#f3eee0",
  Navy: "#1f2a44",
  Oat: "#d9ccb4",
  Ochre: "#c58b2a",
  Plum: "#5e3553",
  Rust: "#a5482b",
  Sage: "#9caf88",
  Sand: "#d6c3a0",
  Slate: "#5b6670",
  White: "#ffffff",
};

export function swatch(colour: string): string | null {
  return Object.hasOwn(SWATCHES, colour) ? (SWATCHES[colour] ?? null) : null;
}
