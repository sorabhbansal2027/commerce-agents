// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  applyChange,
  classifyProduct,
  discardChange,
  enrichProduct,
  fetchBBWProducts,
  generateGeo,
  generateSEO,
  runSPARQLQuery,
  searchEntities,
} from "@/lib/api";
import type {
  ApplyChangeResponse,
  BBWProduct,
  GeoContent,
  KGEntity,
  MerchandisingResult,
  ProductOntology,
  SEOContent,
  SPARQLResult,
} from "@/lib/types";

// ── Sub-types ─────────────────────────────────────────────────────────────────

type MerchandisingTab = "enrich" | "seo" | "geo" | "classify" | "kg";

interface MerchandisingViewProps {
  onAskAssistant: (text: string) => void;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function CategoryBadge({ category }: { category: string }) {
  const colours: Record<string, string> = {
    candles: "bg-amber-100 text-amber-800",
    body_care: "bg-pink-100 text-pink-800",
    home_fragrance: "bg-purple-100 text-purple-800",
    soaps: "bg-teal-100 text-teal-800",
    jewelry: "bg-yellow-100 text-yellow-800",
    accessories: "bg-blue-100 text-blue-800",
  };
  const cls = colours[category] ?? "bg-gray-100 text-gray-700";
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {category.replace("_", " ")}
    </span>
  );
}

function Chip({ label }: { label: string }) {
  return (
    <span className="inline-block rounded-full border border-(--border) bg-(--surface-raised) px-2.5 py-0.5 text-xs text-(--ink-secondary)">
      {label}
    </span>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      onClick={copy}
      className="ml-2 rounded px-1.5 py-0.5 text-xs text-(--ink-secondary) hover:bg-(--surface-raised) transition-colors"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function LoadingSpinner() {
  return (
    <div className="flex items-center gap-2 py-8 justify-center text-(--ink-secondary)">
      <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <span className="text-sm">Gemini is generating content…</span>
    </div>
  );
}

// ── Result panels ─────────────────────────────────────────────────────────────

type ApplyState =
  | { phase: "idle" }
  | { phase: "confirming" }
  | { phase: "applying" }
  | { phase: "applied"; result: ApplyChangeResponse }
  | { phase: "error"; message: string };

function EnrichPanel({
  result,
  originalDescription,
  onStage,
}: {
  result: MerchandisingResult;
  originalDescription: string;
  onStage: () => void;
}) {
  const [applyState, setApplyState] = useState<ApplyState>({ phase: "idle" });

  const handleApply = async () => {
    if (!result.staged_change_id) return;
    setApplyState({ phase: "applying" });
    try {
      const res = await applyChange(result.staged_change_id);
      if (res) {
        setApplyState({ phase: "applied", result: res });
      } else {
        setApplyState({ phase: "error", message: "Apply returned no response — check API logs." });
      }
    } catch (e) {
      setApplyState({ phase: "error", message: String(e) });
    }
  };

  const handleDiscard = async () => {
    if (!result.staged_change_id) return;
    try {
      await discardChange(result.staged_change_id);
    } catch {
      // best-effort
    }
    setApplyState({ phase: "idle" });
    onStage(); // re-run to clear staged state
  };

  return (
    <div className="space-y-5">
      {/* Before / After description diff */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="mb-1 flex items-center gap-1.5">
            <span className="rounded px-1.5 py-0.5 text-xs font-semibold bg-gray-100 text-gray-600">Before</span>
          </div>
          <p className="rounded-lg border border-(--border) bg-(--surface-raised) p-3 text-sm leading-relaxed text-(--ink-secondary) min-h-[80px]">
            {originalDescription || <em className="text-(--ink-secondary)">No description</em>}
          </p>
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="rounded px-1.5 py-0.5 text-xs font-semibold bg-purple-100 text-purple-700">After (AI)</span>
            <CopyButton text={result.enriched_description} />
          </div>
          <p className="rounded-lg border border-purple-200 bg-purple-50 p-3 text-sm leading-relaxed text-(--ink) min-h-[80px]">
            {result.enriched_description}
          </p>
        </div>
      </div>

      {result.benefits_bullets.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-(--ink)">Benefit Bullets</h3>
          <ul className="space-y-1.5">
            {result.benefits_bullets.map((b, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-(--ink)">
                <span className="mt-0.5 shrink-0 text-(--brand)">•</span>
                {b}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.ontology.semantic_attributes.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-(--ink)">Semantic Attributes</h3>
          <div className="overflow-hidden rounded-lg border border-(--border)">
            <table className="w-full text-xs">
              <thead className="bg-(--surface-raised)">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-(--ink-secondary)">Attribute</th>
                  <th className="px-3 py-2 text-left font-medium text-(--ink-secondary)">Value</th>
                  <th className="px-3 py-2 text-left font-medium text-(--ink-secondary)">Confidence</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-(--border)">
                {result.ontology.semantic_attributes.map((attr, i) => (
                  <tr key={i}>
                    <td className="px-3 py-2 font-medium text-(--ink)">{attr.key}</td>
                    <td className="px-3 py-2 text-(--ink)">{attr.value}</td>
                    <td className="px-3 py-2 text-(--ink-secondary)">{Math.round(attr.confidence * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Save to product model section */}
      <div className="rounded-lg border border-(--border) bg-(--surface-raised) p-4 space-y-3">
        <h3 className="text-sm font-semibold text-(--ink)">Save to Product Data Model</h3>

        {!result.staged_change_id && applyState.phase === "idle" && (
          <>
            <p className="text-xs text-(--ink-secondary)">
              Stage this AI-generated content as a pending change, then apply it to write directly to the SFCC product catalog.
            </p>
            <button
              onClick={onStage}
              className="w-full rounded-lg border border-(--brand) bg-(--brand) py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
            >
              Stage Changes
            </button>
          </>
        )}

        {result.staged_change_id && applyState.phase === "idle" && (
          <>
            <div className="flex items-center gap-2 text-xs text-(--ink-secondary)">
              <span className="inline-block h-2 w-2 rounded-full bg-amber-400" />
              Staged — change ID: <code className="font-mono text-(--ink)">{result.staged_change_id}</code>
            </div>
            <p className="text-xs text-(--ink-secondary)">
              Review the before/after above. Click <strong>Apply to SFCC</strong> to write the enriched description to the live product catalog.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setApplyState({ phase: "confirming" })}
                className="flex-1 rounded-lg bg-(--brand) py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
              >
                Apply to SFCC
              </button>
              <button
                onClick={() => void handleDiscard()}
                className="rounded-lg border border-(--border) px-3 py-2 text-sm text-(--ink-secondary) hover:bg-(--surface) transition-colors"
              >
                Discard
              </button>
            </div>
          </>
        )}

        {applyState.phase === "confirming" && (
          <>
            <p className="text-xs font-medium text-amber-700 bg-amber-50 rounded p-2 border border-amber-200">
              This will write the AI-generated description to the live SFCC product record for <strong>{result.listing_id}</strong>. This cannot be undone from this UI.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => void handleApply()}
                className="flex-1 rounded-lg bg-green-600 py-2 text-sm font-medium text-white hover:bg-green-700 transition-colors"
              >
                Confirm — Write to SFCC
              </button>
              <button
                onClick={() => setApplyState({ phase: "idle" })}
                className="rounded-lg border border-(--border) px-3 py-2 text-sm text-(--ink-secondary) hover:bg-(--surface) transition-colors"
              >
                Cancel
              </button>
            </div>
          </>
        )}

        {applyState.phase === "applying" && (
          <div className="flex items-center gap-2 text-sm text-(--ink-secondary)">
            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Writing to SFCC…
          </div>
        )}

        {applyState.phase === "applied" && (
          <div className="rounded-lg bg-green-50 border border-green-200 p-3 space-y-1">
            <p className="text-sm font-medium text-green-800">Applied to SFCC product catalog</p>
            <p className="text-xs text-green-700">
              Change <code className="font-mono">{applyState.result.change_id}</code> — status: {applyState.result.status}
            </p>
            {applyState.result.applied_at && (
              <p className="text-xs text-green-600">{new Date(applyState.result.applied_at).toLocaleString()}</p>
            )}
          </div>
        )}

        {applyState.phase === "error" && (
          <div className="rounded-lg bg-red-50 border border-red-200 p-3">
            <p className="text-sm font-medium text-red-800">Apply failed</p>
            <p className="text-xs text-red-700 mt-1">{applyState.message}</p>
            <button
              onClick={() => setApplyState({ phase: "idle" })}
              className="mt-2 text-xs text-red-600 underline"
            >
              Dismiss
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function SEOPanel({ seo }: { seo: SEOContent }) {
  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1 flex items-center justify-between">
          <label className="text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">SEO Title</label>
          <span className={`text-xs ${seo.title.length > 60 ? "text-red-500" : "text-(--ink-secondary)"}`}>
            {seo.title.length}/60
          </span>
        </div>
        <div className="flex items-start gap-2 rounded-lg border border-(--border) bg-(--surface-raised) p-3">
          <span className="flex-1 text-sm font-medium text-(--ink)">{seo.title}</span>
          <CopyButton text={seo.title} />
        </div>
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <label className="text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">Meta Description</label>
          <span className={`text-xs ${seo.meta_description.length > 155 ? "text-red-500" : "text-(--ink-secondary)"}`}>
            {seo.meta_description.length}/155
          </span>
        </div>
        <div className="flex items-start gap-2 rounded-lg border border-(--border) bg-(--surface-raised) p-3">
          <span className="flex-1 text-sm text-(--ink)">{seo.meta_description}</span>
          <CopyButton text={seo.meta_description} />
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">H1</label>
        <div className="flex items-start gap-2 rounded-lg border border-(--border) bg-(--surface-raised) p-3">
          <span className="flex-1 text-sm text-(--ink)">{seo.h1}</span>
          <CopyButton text={seo.h1} />
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">URL Slug</label>
        <div className="flex items-center gap-2 rounded-lg border border-(--border) bg-(--surface-raised) p-3">
          <code className="flex-1 text-sm text-(--ink-secondary)">/products/{seo.slug}</code>
          <CopyButton text={seo.slug} />
        </div>
      </div>

      {seo.keywords.length > 0 && (
        <div>
          <label className="mb-2 block text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">Keywords</label>
          <div className="flex flex-wrap gap-1.5">
            {seo.keywords.map((kw, i) => (
              <Chip key={i} label={kw} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function GeoPanel({ variants }: { variants: GeoContent[] }) {
  const regionLabels: Record<string, string> = {
    "US-northeast": "🇺🇸 US Northeast",
    "US-south": "🇺🇸 US South",
    "US-west": "🇺🇸 US West",
    UK: "🇬🇧 United Kingdom",
    CA: "🇨🇦 Canada",
  };

  return (
    <div className="space-y-4">
      {variants.map((v, i) => (
        <div key={i} className="rounded-lg border border-(--border) p-4">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold text-(--ink)">
              {regionLabels[v.region] ?? v.region}
            </span>
            <CopyButton text={v.description_variant} />
          </div>

          {v.title_variant && (
            <p className="mb-2 text-xs font-medium text-(--ink-secondary)">{v.title_variant}</p>
          )}

          <p className="mb-3 text-sm leading-relaxed text-(--ink)">{v.description_variant}</p>

          {v.occasion_tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {v.occasion_tags.map((tag, j) => (
                <Chip key={j} label={tag} />
              ))}
            </div>
          )}

          {v.seasonal_notes && (
            <p className="mt-2 text-xs italic text-(--ink-secondary)">{v.seasonal_notes}</p>
          )}
        </div>
      ))}
    </div>
  );
}

function ClassifyPanel({ ontology }: { ontology: ProductOntology }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {ontology.category && <CategoryBadge category={ontology.category} />}
        {ontology.subcategory && (
          <span className="rounded-full border border-(--border) px-2.5 py-0.5 text-xs text-(--ink-secondary)">
            {ontology.subcategory}
          </span>
        )}
      </div>

      {ontology.target_persona && (
        <div>
          <label className="mb-1 block text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">Target Persona</label>
          <p className="text-sm font-medium text-(--ink)">{ontology.target_persona}</p>
        </div>
      )}

      {ontology.occasions.length > 0 && (
        <div>
          <label className="mb-2 block text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">Occasions</label>
          <div className="flex flex-wrap gap-1.5">
            {ontology.occasions.map((o, i) => <Chip key={i} label={o} />)}
          </div>
        </div>
      )}

      {ontology.style_tags.length > 0 && (
        <div>
          <label className="mb-2 block text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">Style Tags</label>
          <div className="flex flex-wrap gap-1.5">
            {ontology.style_tags.map((t, i) => <Chip key={i} label={t} />)}
          </div>
        </div>
      )}

      {ontology.benefits.length > 0 && (
        <div>
          <label className="mb-1 block text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">Benefits</label>
          <ul className="space-y-1">
            {ontology.benefits.map((b, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-(--ink)">
                <span className="mt-0.5 shrink-0 text-(--brand)">✓</span> {b}
              </li>
            ))}
          </ul>
        </div>
      )}

      {ontology.semantic_attributes.length > 0 && (
        <div>
          <label className="mb-2 block text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">Semantic Attributes</label>
          <div className="overflow-hidden rounded-lg border border-(--border)">
            <table className="w-full text-xs">
              <thead className="bg-(--surface-raised)">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-(--ink-secondary)">Key</th>
                  <th className="px-3 py-2 text-left font-medium text-(--ink-secondary)">Value</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-(--border)">
                {ontology.semantic_attributes.map((attr, i) => (
                  <tr key={i}>
                    <td className="px-3 py-2 font-medium text-(--ink)">{attr.key}</td>
                    <td className="px-3 py-2 text-(--ink)">{attr.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Knowledge Graph panel ─────────────────────────────────────────────────────

function KGEntityCard({ entity }: { entity: KGEntity }) {
  return (
    <div className="rounded-lg border border-(--border) p-4 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-(--ink)">{entity.name}</span>
            {entity.entity_id && (
              <a
                href={
                  entity.entity_id.startsWith("kg:")
                    ? `https://www.google.com/search?kgmid=${entity.entity_id.replace("kg:", "")}`
                    : `https://www.wikidata.org/wiki/${entity.entity_id}`
                }
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-full bg-violet-100 text-violet-700 px-2 py-0.5 text-xs font-mono hover:bg-violet-200 transition-colors"
              >
                {entity.entity_id.startsWith("kg:") ? entity.entity_id.replace("kg:/m/", "mid:") : entity.entity_id}
              </a>
            )}
            {entity.provider === "wikidata" && (
              <span className="rounded-full bg-blue-50 text-blue-600 border border-blue-200 px-2 py-0.5 text-xs">
                Open Entity Graph
              </span>
            )}
            {entity.provider === "google_knowledge_graph" && (
              <span className="rounded-full bg-green-50 text-green-700 border border-green-200 px-2 py-0.5 text-xs">
                Enterprise Entity Graph
              </span>
            )}
          </div>
          {entity.description && (
            <p className="mt-1 text-sm text-(--ink-secondary)">{entity.description}</p>
          )}
        </div>
        {entity.url && (
          <a
            href={entity.url}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 rounded-md border border-(--border) px-2.5 py-1 text-xs text-(--ink-secondary) hover:bg-(--surface-raised) transition-colors"
          >
            Wikipedia ↗
          </a>
        )}
      </div>
      {entity.types.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {entity.types.map((t, i) => (
            <Chip key={i} label={t} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── SPARQL preset queries (mirrors SPARQL_PRESETS in merchandising.py) ─────────

const SPARQL_PRESETS_MAP: Record<string, { label: string; query: string }> = {
  botanical: {
    label: "🌿 Botanical extracts",
    query:
      "# Essential oils and plant extracts used in fragrance\nSELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n  { ?item wdt:P279* wd:Q381165 }\n  UNION\n  { ?item wdt:P279* wd:Q162828 }\n  FILTER NOT EXISTS { ?item wdt:P31 wd:Q4167410 }\n  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n} LIMIT 12",
  },
  floral: {
    label: "🌸 Floral ingredients",
    query:
      "# Flowering plants used as fragrance ingredients\nSELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n  ?item wdt:P31/wdt:P279* wd:Q506 .\n  ?item wdt:P18 [] .\n  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n} LIMIT 12",
  },
  wood: {
    label: "🪵 Wood materials",
    query:
      "# Wood species used in fragrance and materials\nSELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n  ?item wdt:P31/wdt:P279* wd:Q287 .\n  ?item wdt:P18 [] .\n  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n} LIMIT 12",
  },
  citrus: {
    label: "🍋 Citrus compounds",
    query:
      "# Terpene compounds found in citrus plants\nSELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n  ?item wdt:P279* wd:Q131524 .\n  ?item wdt:P703 ?plant .\n  ?plant wdt:P171* wd:Q19704 .\n  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n} LIMIT 12",
  },
  grasses: {
    label: "🌾 Aromatic grasses",
    query:
      "# Plants in the Poaceae (grass) family\nSELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n  ?item wdt:P31/wdt:P279* wd:Q756 .\n  ?item wdt:P171* wd:Q46078 .\n  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n} LIMIT 12",
  },
  skin: {
    label: "🧴 Skin care actives",
    query:
      "# Fatty acids and lipids used in cosmetics\nSELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n  { ?item wdt:P279* wd:Q61476 }\n  UNION\n  { ?item wdt:P279* wd:Q18534 }\n  ?item wdt:P18 [] .\n  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n} LIMIT 12",
  },
  wax: {
    label: "🕯️ Wax & carriers",
    query:
      "# Waxes and waxy substances used in candles and cosmetics\nSELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n  ?item wdt:P279* wd:Q124695 .\n  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n} LIMIT 12",
  },
  resinous: {
    label: "🌲 Resinous notes",
    query:
      "# Plant resins used in incense and perfumery\nSELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n  ?item wdt:P279* wd:Q145740 .\n  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n} LIMIT 12",
  },
};

function SPARQLResultsTable({ result }: { result: SPARQLResult }) {
  if (result.rows.length === 0) {
    return (
      <p className="text-xs text-(--ink-faint) py-6 text-center">
        Query returned no results — try modifying the SPARQL above.
      </p>
    );
  }
  const toWikidataUrl = (val: string) =>
    /^Q\d+$/.test(val) ? `https://www.wikidata.org/wiki/${val}` : null;

  return (
    <div className="overflow-x-auto rounded-lg border border-(--border) text-xs">
      <table className="w-full border-collapse">
        <thead>
          <tr className="bg-(--surface-raised) border-b border-(--border)">
            {result.columns.map((col) => (
              <th key={col} className="px-3 py-2 text-left font-medium text-(--ink-secondary) whitespace-nowrap">
                ?{col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-(--border)">
          {result.rows.map((row, i) => (
            <tr key={i} className="hover:bg-(--surface-raised) transition-colors">
              {result.columns.map((col) => {
                const val = row[col] ?? "";
                const href = toWikidataUrl(val);
                return (
                  <td key={col} className="px-3 py-2 text-(--ink) max-w-xs">
                    {href ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-mono text-violet-600 hover:underline"
                      >
                        {val}
                      </a>
                    ) : (
                      <span className="line-clamp-2" title={val}>{val}</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function KGPanel({ product }: { product: BBWProduct }) {
  const [mode, setMode] = useState<"search" | "sparql">("search");

  // Search mode state
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KGEntity[] | null>(null);
  const [source, setSource] = useState("");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // SPARQL mode state
  const [sparqlQuery, setSparqlQuery] = useState(SPARQL_PRESETS_MAP.botanical.query);
  const [sparqlRunning, setSparqlRunning] = useState(false);
  const [sparqlResult, setSparqlResult] = useState<SPARQLResult | null>(null);
  const [sparqlError, setSparqlError] = useState<string | null>(null);

  // Quick-fill terms derived from the product's attributes
  const quickTerms: string[] = [];
  const attrs = product.attributes ?? {};
  if (attrs.scent_notes) attrs.scent_notes.split(",").slice(0, 3).forEach((n) => quickTerms.push(n.trim()));
  if (attrs.key_ingredient) attrs.key_ingredient.split(",").slice(0, 2).forEach((n) => quickTerms.push(n.trim()));
  if (attrs.fragrance_family && !quickTerms.includes(attrs.fragrance_family)) quickTerms.push(attrs.fragrance_family);
  if (quickTerms.length === 0) quickTerms.push(product.title);

  const runSearch = async (q: string) => {
    const term = q.trim();
    if (!term) return;
    setQuery(term);
    setSearching(true);
    setError(null);
    setResults(null);
    try {
      const res = await searchEntities(term, 5);
      if (res) {
        setResults(res.entities);
        setSource(res.source);
      } else {
        setError("No response from the entities API — check the merchant API connection.");
      }
    } catch {
      setError("Request failed — check the merchant API connection.");
    } finally {
      setSearching(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void runSearch(query);
  };

  const runSPARQL = async () => {
    const q = sparqlQuery.trim();
    if (!q) return;
    setSparqlRunning(true);
    setSparqlError(null);
    setSparqlResult(null);
    try {
      const res = await runSPARQLQuery(q);
      if (res) {
        setSparqlResult(res);
      } else {
        setSparqlError("No response from the SPARQL endpoint.");
      }
    } catch {
      setSparqlError("SPARQL query failed — check syntax or try a simpler query.");
    } finally {
      setSparqlRunning(false);
    }
  };

  // Category preset entries used for both search and SPARQL modes
  const categoryPresets = Object.entries(SPARQL_PRESETS_MAP).map(([key, p]) => ({
    key,
    label: p.label,
    sparqlQuery: p.query,
    searchTerm: ({ botanical: "eucalyptus", floral: "jasmine", wood: "sandalwood", citrus: "bergamot", grasses: "vetiver", skin: "shea butter", wax: "soy wax", resinous: "frankincense" } as Record<string, string>)[key] ?? key,
  }));

  return (
    <div className="space-y-4">
      {/* Mode toggle */}
      <div className="flex items-center justify-between">
        <p className="text-xs text-(--ink-secondary)">
          {mode === "search"
            ? "Search the knowledge graph for real-world ingredient facts."
            : "Write and run SPARQL queries against the Wikidata endpoint."}
        </p>
        <div className="flex rounded-lg border border-(--border) overflow-hidden text-xs font-medium">
          <button
            onClick={() => setMode("search")}
            className={`px-3 py-1.5 transition-colors ${mode === "search" ? "bg-(--brand) text-white" : "bg-(--surface) text-(--ink-secondary) hover:bg-(--surface-raised)"}`}
          >
            Search
          </button>
          <button
            onClick={() => setMode("sparql")}
            className={`px-3 py-1.5 transition-colors ${mode === "sparql" ? "bg-(--brand) text-white" : "bg-(--surface) text-(--ink-secondary) hover:bg-(--surface-raised)"}`}
          >
            SPARQL
          </button>
        </div>
      </div>

      {mode === "search" ? (
        <>
          {/* Search form */}
          <form onSubmit={handleSubmit} className="flex gap-2">
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. mahogany wood, eucalyptus, shea butter…"
              className="flex-1 rounded-lg border border-(--border) bg-(--surface) px-3 py-2 text-sm text-(--ink) placeholder:text-(--ink-faint) focus:outline-none focus:ring-2 focus:ring-(--brand)"
            />
            <button
              type="submit"
              disabled={!query.trim() || searching}
              className="rounded-lg bg-(--brand) px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {searching ? "Searching…" : "Search"}
            </button>
          </form>

          {/* Quick-fill chips from product attributes */}
          <div>
            <p className="mb-1.5 text-xs font-medium text-(--ink-secondary)">Quick-fill from this product:</p>
            <div className="flex flex-wrap gap-1.5">
              {quickTerms.map((term, i) => (
                <button
                  key={i}
                  onClick={() => void runSearch(term)}
                  className="rounded-full border border-(--border) bg-(--surface-raised) px-2.5 py-0.5 text-xs text-(--ink-secondary) hover:bg-(--surface) hover:text-(--ink) transition-colors"
                >
                  {term}
                </button>
              ))}
            </div>
          </div>

          {/* Category presets */}
          <div className="rounded-lg border border-(--border) bg-(--surface-raised) p-3">
            <p className="mb-2 text-xs font-medium text-(--ink-secondary)">Browse by category:</p>
            <div className="grid grid-cols-2 gap-2">
              {categoryPresets.map(({ key, label, searchTerm }) => (
                <button
                  key={key}
                  onClick={() => void runSearch(searchTerm)}
                  className="rounded-md border border-(--border) bg-(--surface) px-3 py-2 text-left text-xs text-(--ink-secondary) hover:border-(--brand) hover:text-(--ink) transition-colors"
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Search results */}
          {searching && (
            <div className="flex items-center gap-2 py-6 justify-center text-(--ink-secondary)">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <span className="text-sm">Querying knowledge graph…</span>
            </div>
          )}
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
          )}
          {results !== null && !searching && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs text-(--ink-secondary)">
                  {results.length === 0
                    ? `No entities found for "${query}"`
                    : `${results.length} ${results.length === 1 ? "entity" : "entities"} for "${query}"`}
                </p>
                {source && (
                  <span className="text-xs text-(--ink-faint)">
                    via {source === "wikidata" ? "Open Entity Graph" : "Enterprise Entity Graph"}
                  </span>
                )}
              </div>
              {results.map((entity, i) => (
                <KGEntityCard key={i} entity={entity} />
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          {/* SPARQL editor */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-xs font-medium text-(--ink-secondary)">SPARQL query (Wikidata endpoint)</p>
              <a
                href="https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service/Wikidata_Query_Help"
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-(--ink-faint) hover:text-(--ink) transition-colors"
              >
                Query help ↗
              </a>
            </div>
            <textarea
              value={sparqlQuery}
              onChange={(e) => setSparqlQuery(e.target.value)}
              rows={10}
              spellCheck={false}
              className="w-full rounded-lg border border-(--border) bg-(--surface) px-3 py-2 text-xs font-mono text-(--ink) focus:outline-none focus:ring-2 focus:ring-(--brand) resize-y"
            />
            <button
              onClick={() => void runSPARQL()}
              disabled={!sparqlQuery.trim() || sparqlRunning}
              className="rounded-lg bg-(--brand) px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {sparqlRunning ? "Running…" : "Run Query"}
            </button>
          </div>

          {/* Category presets — load SPARQL into editor */}
          <div className="rounded-lg border border-(--border) bg-(--surface-raised) p-3">
            <p className="mb-2 text-xs font-medium text-(--ink-secondary)">Load preset query:</p>
            <div className="grid grid-cols-2 gap-2">
              {categoryPresets.map(({ key, label, sparqlQuery: pq }) => (
                <button
                  key={key}
                  onClick={() => { setSparqlQuery(pq); setSparqlResult(null); setSparqlError(null); }}
                  className="rounded-md border border-(--border) bg-(--surface) px-3 py-2 text-left text-xs text-(--ink-secondary) hover:border-(--brand) hover:text-(--ink) transition-colors"
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* SPARQL results */}
          {sparqlRunning && (
            <div className="flex items-center gap-2 py-6 justify-center text-(--ink-secondary)">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <span className="text-sm">Querying Wikidata SPARQL endpoint…</span>
            </div>
          )}
          {sparqlError && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{sparqlError}</div>
          )}
          {sparqlResult && !sparqlRunning && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs text-(--ink-secondary)">
                  {sparqlResult.rows.length} {sparqlResult.rows.length === 1 ? "row" : "rows"} returned
                </p>
                {sparqlResult.elapsed_ms !== undefined && (
                  <span className="text-xs text-(--ink-faint)">{sparqlResult.elapsed_ms} ms · Wikidata</span>
                )}
              </div>
              <SPARQLResultsTable result={sparqlResult} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function MerchandisingView({ onAskAssistant }: MerchandisingViewProps) {
  const [products, setProducts] = useState<BBWProduct[]>([]);
  const [selectedProduct, setSelectedProduct] = useState<BBWProduct | null>(null);
  const [activeTab, setActiveTab] = useState<MerchandisingTab>("enrich");
  const [loading, setLoading] = useState(false);
  const [loadingProducts, setLoadingProducts] = useState(true);

  // Results for each tab — cached per product
  const [enrichResult, setEnrichResult] = useState<MerchandisingResult | null>(null);
  const [seoResult, setSeoResult] = useState<SEOContent | null>(null);
  const [geoResult, setGeoResult] = useState<GeoContent[] | null>(null);
  const [classifyResult, setClassifyResult] = useState<ProductOntology | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoadingProducts(true);
    fetchBBWProducts()
      .then((res) => {
        if (res?.products) setProducts(res.products);
      })
      .finally(() => setLoadingProducts(false));
  }, []);

  const selectProduct = (product: BBWProduct) => {
    setSelectedProduct(product);
    // Clear results when switching products
    setEnrichResult(null);
    setSeoResult(null);
    setGeoResult(null);
    setClassifyResult(null);
    setError(null);
  };

  const runTab = useCallback(
    async (tab: MerchandisingTab) => {
      if (!selectedProduct) return;
      setActiveTab(tab);
      setError(null);

      // KG tab is self-contained (has its own search state)
      if (tab === "kg") return;

      // Return cached result if available
      if (tab === "enrich" && enrichResult) return;
      if (tab === "seo" && seoResult) return;
      if (tab === "geo" && geoResult) return;
      if (tab === "classify" && classifyResult) return;

      setLoading(true);
      try {
        const id = selectedProduct.id;
        if (tab === "enrich") {
          const res = await enrichProduct(id);
          if (res) setEnrichResult(res);
          else setError("Enrichment failed — check API connection.");
        } else if (tab === "seo") {
          const res = await generateSEO(id);
          if (res) setSeoResult(res);
          else setError("SEO generation failed — check API connection.");
        } else if (tab === "geo") {
          const res = await generateGeo(id, "US-northeast,US-south,US-west,UK");
          if (res) setGeoResult(res.variants);
          else setError("Geo content generation failed — check API connection.");
        } else if (tab === "classify") {
          const res = await classifyProduct(id);
          if (res) setClassifyResult(res);
          else setError("Classification failed — check API connection.");
        }
      } catch (e) {
        setError("Request failed — check that the merchant API is reachable.");
      } finally {
        setLoading(false);
      }
    },
    [selectedProduct, enrichResult, seoResult, geoResult, classifyResult],
  );

  const handleStage = useCallback(async () => {
    if (!selectedProduct) return;
    setLoading(true);
    try {
      const res = await enrichProduct(selectedProduct.id, true);
      if (res) setEnrichResult(res);
    } finally {
      setLoading(false);
    }
  }, [selectedProduct, enrichResult]);

  const EXAMPLE_PROMPTS = [
    "Enrich all body care listings for the holiday gift guide",
    "Generate SEO titles for our top candle products for US market",
    "Adapt the Mahogany Teakwood Candle description for UK buyers",
    "Classify our catalog by occasion so we can build collections",
  ];

  const TABS: { id: MerchandisingTab; label: string }[] = [
    { id: "enrich", label: "Enrich" },
    { id: "seo", label: "SEO" },
    { id: "geo", label: "Geo" },
    { id: "classify", label: "Classify" },
    { id: "kg", label: "Knowledge Graph" },
  ];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 border-b border-(--border) px-6 py-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold text-(--ink)">Merchandising</h1>
            <p className="mt-0.5 text-sm text-(--ink-secondary)">
              AI-powered content enrichment, SEO, geo localisation, and ontology classification
            </p>
          </div>
        </div>

        {/* Example prompts */}
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLE_PROMPTS.map((prompt, i) => (
            <button
              key={i}
              onClick={() => onAskAssistant(prompt)}
              className="rounded-full border border-(--border) bg-(--surface-raised) px-3 py-1 text-xs text-(--ink-secondary) hover:bg-(--surface) hover:text-(--ink) transition-colors"
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>

      {/* Body: product list + results */}
      <div className="flex min-h-0 flex-1">
        {/* Left: product list */}
        <div className="w-72 shrink-0 overflow-y-auto border-r border-(--border)">
          <div className="px-4 py-3">
            <p className="text-xs font-medium text-(--ink-secondary) uppercase tracking-wide">BBW Product Catalog</p>
          </div>

          {loadingProducts ? (
            <div className="flex items-center justify-center py-8">
              <span className="text-sm text-(--ink-secondary)">Loading products…</span>
            </div>
          ) : (
            <ul className="divide-y divide-(--border)">
              {products.map((product) => (
                <li key={product.id}>
                  <button
                    onClick={() => selectProduct(product)}
                    className={`w-full px-4 py-3 text-left transition-colors hover:bg-(--surface-raised) ${
                      selectedProduct?.id === product.id ? "bg-(--surface-raised) border-l-2 border-(--brand)" : ""
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-(--ink)">{product.title}</p>
                        <div className="mt-1 flex items-center gap-2">
                          <CategoryBadge category={product.category} />
                          <span className="text-xs text-(--ink-secondary)">
                            {product.currency} {product.price.toFixed(2)}
                          </span>
                        </div>
                      </div>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Right: results panel */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {!selectedProduct ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
              <div className="rounded-full bg-(--surface-raised) p-4">
                <svg className="h-8 w-8 text-(--ink-secondary)" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
                </svg>
              </div>
              <p className="text-sm font-medium text-(--ink)">Select a product to start</p>
              <p className="max-w-sm text-xs text-(--ink-secondary)">
                Choose a product from the list, then use the tabs to generate enriched descriptions, SEO content, geo variants, or ontology classifications.
              </p>
            </div>
          ) : (
            <>
              {/* Product header */}
              <div className="shrink-0 border-b border-(--border) px-6 py-4">
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate text-base font-semibold text-(--ink)">{selectedProduct.title}</h2>
                    <div className="mt-1 flex items-center gap-2">
                      <CategoryBadge category={selectedProduct.category} />
                      <span className="text-xs text-(--ink-secondary)">{selectedProduct.id}</span>
                      <span className="text-xs text-(--ink-secondary)">·</span>
                      <span className="text-xs text-(--ink-secondary)">
                        {selectedProduct.currency} {selectedProduct.price.toFixed(2)}
                      </span>
                    </div>
                  </div>
                </div>

                {selectedProduct.description && (
                  <p className="mt-2 text-xs text-(--ink-secondary) line-clamp-2">{selectedProduct.description}</p>
                )}

                {/* Tabs */}
                <div className="mt-3 flex gap-1">
                  {TABS.map((tab) => (
                    <button
                      key={tab.id}
                      onClick={() => void runTab(tab.id)}
                      className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                        activeTab === tab.id
                          ? "bg-(--brand) text-white"
                          : "text-(--ink-secondary) hover:bg-(--surface-raised) hover:text-(--ink)"
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Results */}
              <div className="flex-1 overflow-y-auto px-6 py-4">
                {loading ? (
                  <LoadingSpinner />
                ) : error ? (
                  <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                    {error}
                  </div>
                ) : (
                  <>
                    {activeTab === "enrich" && enrichResult && (
                      <EnrichPanel
                        result={enrichResult}
                        originalDescription={selectedProduct.description ?? ""}
                        onStage={() => void handleStage()}
                      />
                    )}
                    {activeTab === "enrich" && !enrichResult && (
                      <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                        <p className="text-sm text-(--ink-secondary)">Click Enrich to generate content for this product.</p>
                        <button
                          onClick={() => void runTab("enrich")}
                          className="rounded-lg bg-(--brand) px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
                        >
                          Generate Enriched Content
                        </button>
                      </div>
                    )}

                    {activeTab === "seo" && seoResult && <SEOPanel seo={seoResult} />}
                    {activeTab === "seo" && !seoResult && (
                      <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                        <p className="text-sm text-(--ink-secondary)">Click SEO to generate search-optimised content.</p>
                        <button
                          onClick={() => void runTab("seo")}
                          className="rounded-lg bg-(--brand) px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
                        >
                          Generate SEO Content
                        </button>
                      </div>
                    )}

                    {activeTab === "geo" && geoResult && <GeoPanel variants={geoResult} />}
                    {activeTab === "geo" && !geoResult && (
                      <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                        <p className="text-sm text-(--ink-secondary)">Click Geo to generate regional content variants.</p>
                        <button
                          onClick={() => void runTab("geo")}
                          className="rounded-lg bg-(--brand) px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
                        >
                          Generate Geo Variants
                        </button>
                      </div>
                    )}

                    {activeTab === "classify" && classifyResult && (
                      <ClassifyPanel ontology={classifyResult} />
                    )}
                    {activeTab === "classify" && !classifyResult && (
                      <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                        <p className="text-sm text-(--ink-secondary)">Click Classify to run ontology classification.</p>
                        <button
                          onClick={() => void runTab("classify")}
                          className="rounded-lg bg-(--brand) px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
                        >
                          Classify Product
                        </button>
                      </div>
                    )}

                    {activeTab === "kg" && (
                      <KGPanel key={selectedProduct.id} product={selectedProduct} />
                    )}
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
