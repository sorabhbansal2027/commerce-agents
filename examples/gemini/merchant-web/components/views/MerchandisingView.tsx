// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  classifyProduct,
  enrichProduct,
  fetchBBWProducts,
  generateGeo,
  generateSEO,
} from "@/lib/api";
import type { BBWProduct, GeoContent, MerchandisingResult, ProductOntology, SEOContent } from "@/lib/types";

// ── Sub-types ─────────────────────────────────────────────────────────────────

type MerchandisingTab = "enrich" | "seo" | "geo" | "classify";

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

function EnrichPanel({ result, onStage }: { result: MerchandisingResult; onStage: () => void }) {
  return (
    <div className="space-y-5">
      <div>
        <div className="mb-1 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-(--ink)">Enriched Description</h3>
          <CopyButton text={result.enriched_description} />
        </div>
        <p className="rounded-lg border border-(--border) bg-(--surface-raised) p-3 text-sm leading-relaxed text-(--ink)">
          {result.enriched_description}
        </p>
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

      {result.staged_change_id ? (
        <div className="rounded-lg bg-green-50 border border-green-200 p-3 text-sm text-green-800">
          Change staged: <code className="font-mono">{result.staged_change_id}</code>
        </div>
      ) : (
        <button
          onClick={onStage}
          className="w-full rounded-lg border border-(--brand) bg-(--brand) py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
        >
          Stage Changes
        </button>
      )}
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
  }, [selectedProduct]);

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
                      <EnrichPanel result={enrichResult} onStage={handleStage} />
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
