// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { AgentApi } from "web-shared";
import type {
  AlertsResponse,
  BBWProduct,
  GeoContent,
  ListingDetailResponse,
  ListingsResponse,
  MerchandisingResult,
  OverviewResponse,
  ProductOntology,
  QuoteApprovalsResponse,
  SEOContent,
  StagedChange,
  StagedChangesResponse,
  ApplyChangeResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const api = new AgentApi(API_URL, "/api/merchant");

export const UNREACHABLE =
  "Couldn't reach the merchant API. Make sure NEXT_PUBLIC_API_URL points to the running merchant-api service.";

export function fetchOverview(): Promise<OverviewResponse | null> {
  return api.get<OverviewResponse>("/overview");
}

export function fetchListings(query?: string): Promise<ListingsResponse | null> {
  return api.get<ListingsResponse>("/listings", query ? { query } : undefined);
}

export function fetchListingDetail(listingId: string): Promise<ListingDetailResponse | null> {
  return api.get<ListingDetailResponse>(`/listings/${encodeURIComponent(listingId)}`);
}

export function fetchAlerts(): Promise<AlertsResponse | null> {
  return api.get<AlertsResponse>("/alerts");
}

export function fetchQuoteApprovals(): Promise<QuoteApprovalsResponse | null> {
  return api.get<QuoteApprovalsResponse>("/quote-approvals");
}

export function approveQuote(workitemId: string, comments = ""): Promise<unknown> {
  return api.post(`/quote-approvals/${encodeURIComponent(workitemId)}/approve`, { comments });
}

export function rejectQuote(workitemId: string, comments = ""): Promise<unknown> {
  return api.post(`/quote-approvals/${encodeURIComponent(workitemId)}/reject`, { comments });
}

// --- Merchandising ---

export function fetchBBWProducts(): Promise<{ products: BBWProduct[] } | null> {
  return api.get<{ products: BBWProduct[] }>("/merchandising/products");
}

export function enrichProduct(listingId: string, autoStage = false): Promise<MerchandisingResult | null> {
  return api.post<MerchandisingResult>("/merchandising/enrich", {
    listing_id: listingId,
    aspects: "all",
    auto_stage: autoStage,
  });
}

export function generateSEO(listingId: string, market = "US", brand = "DreamHaus"): Promise<SEOContent | null> {
  return api.post<SEOContent>("/merchandising/seo", { listing_id: listingId, market, brand });
}

export function generateGeo(listingId: string, regions = "US-northeast,US-south,US-west,UK"): Promise<{ variants: GeoContent[] } | null> {
  return api.post<{ variants: GeoContent[] }>("/merchandising/geo", { listing_id: listingId, regions });
}

export function classifyProduct(listingId: string): Promise<ProductOntology | null> {
  return api.get<ProductOntology>(`/merchandising/classify/${encodeURIComponent(listingId)}`);
}

export function bulkEnrich(query = "", limit = 5): Promise<{ results: MerchandisingResult[] } | null> {
  return api.post<{ results: MerchandisingResult[] }>("/merchandising/bulk", { query, limit });
}

export function fetchStagedChanges(): Promise<StagedChangesResponse | null> {
  return api.get<StagedChangesResponse>("/merchandising/changes");
}

export function applyChange(changeId: string): Promise<ApplyChangeResponse | null> {
  return api.post<ApplyChangeResponse>("/merchandising/apply", { change_id: changeId });
}

export function discardChange(changeId: string): Promise<{ change_id: string; status: string } | null> {
  return api.post<{ change_id: string; status: string }>(
    `/merchandising/changes/${encodeURIComponent(changeId)}/discard`,
    {},
  );
}

export function searchEntities(query: string, limit = 5): Promise<import("./types").KGEntitiesResponse | null> {
  return api.get("/merchandising/entities", { q: query, limit: String(limit) });
}

export function runSPARQLQuery(query: string): Promise<import("./types").SPARQLResult | null> {
  return api.post<import("./types").SPARQLResult>("/merchandising/kg/sparql", { query });
}
