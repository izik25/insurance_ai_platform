// Relative paths: in production nginx proxies these to the backend
// container (see frontend/nginx.conf), and in local dev the Vite dev
// server proxies them to localhost:8000 (see vite.config.ts) - so this
// works unchanged in both environments.
const API_BASE = "/api";
const PUBLIC_API_BASE = "/public/v1";

export interface DocumentOut {
  id: string;
  company_id: string;
  domain: string;
  original_file_name: string;
  appendix_number: string[];
  appendix_name: string | null;
  pages_count: number | null;
  extraction_method: string;
  has_extraction: boolean;
  has_embedding: boolean;
  marketing_start_date: string | null;
  marketing_end_date: string | null;
  is_active: boolean;
  category_id: string | null;
  main_category: string | null;
  coverage_family: string | null;
  coverage_subtype: string | null;
  created_date: string;
}

export interface ClassificationOut {
  category_id: string;
  main_category: string;
  coverage_family: string;
  coverage_subtype: string | null;
  coverage_variant: string | null;
  benefit_model: string | null;
  target_population: string | null;
  confidence: number | null;
  evidence: string | null;
}

export interface CanonicalProfileOut {
  insured_event: string | null;
  covered_events: string[];
  covered_conditions: string[];
  exclusions_normalized: string[];
  limitations: string[];
  eligibility_normalized: string | null;
  waiting_period_text: string | null;
  qualifying_period_text: string | null;
  survival_period_text: string | null;
  benefit_type: string | null;
  benefit_calculation: string | null;
  amounts: string[];
  caps: string[];
  additional_findings_summary: string | null;
}

export interface FingerprintOut {
  waiting_period_days: number | null;
  qualifying_period_days: number | null;
  survival_period_days: number | null;
  min_entry_age: number | null;
  max_entry_age: number | null;
  termination_age: number | null;
  benefit_type: string | null;
  benefit_amount_min: number | null;
  benefit_amount_max: number | null;
  benefit_amount_currency: string | null;
  benefit_percentage: number | null;
  maximum_benefit: number | null;
  deductible_amount: number | null;
  covered_event_count: number;
  major_exclusion_count: number;
  special_condition_count: number;
}

export interface DocumentAnalysisOut {
  document_id: string;
  classification: ClassificationOut | null;
  canonical_profile: CanonicalProfileOut | null;
  fingerprint: FingerprintOut | null;
}

export interface PolicyTableOut {
  title: string | null;
  headers: string[];
  rows: string[][];
}

export interface ExtractionOut {
  document_id: string;
  appendix_number: string[];
  appendix_name: string | null;
  coverage_type: string | null;
  coverage_name: string | null;
  eligibility_conditions: string | null;
  insurance_amounts: string[];
  qualifying_period: string | null;
  waiting_period: string | null;
  exclusions: string[];
  age_range: string | null;
  restrictions: string[];
  tables: PolicyTableOut[];
  disease_count: number | null;
  disease_list: string[];
  survival_period: string | null;
  created_date: string;
}

export interface MatchDocumentSummary {
  id: string;
  company_id: string;
  domain: string;
  original_file_name: string;
  appendix_number: string[];
  appendix_name: string | null;
}

export interface MatchOut {
  id: string;
  document: MatchDocumentSummary;
  matched_document: MatchDocumentSummary;
  similarity_score: number;
  status: string;
  created_date: string;
}

export async function fetchDocuments(): Promise<DocumentOut[]> {
  const response = await fetch(`${API_BASE}/documents`);
  if (!response.ok) throw new Error(`Failed to load documents: ${response.status}`);
  return response.json();
}

export async function fetchAnalysis(documentId: string): Promise<DocumentAnalysisOut | null> {
  // Same {..:path} encoding as fetchExtraction/getDocumentFileUrl - document
  // ids contain literal "/".
  const encodedPath = documentId.split("/").map(encodeURIComponent).join("/");
  const response = await fetch(`${API_BASE}/documents/${encodedPath}/analysis`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Failed to load analysis: ${response.status}`);
  return response.json();
}

export async function fetchExtraction(documentId: string): Promise<ExtractionOut | null> {
  // document_id contains literal "/" (e.g. "phoenix:phoenix/health/x.pdf") and the
  // backend route uses a {document_id:path} converter, which expects raw "/" as
  // path separators - encode each segment but leave the slashes themselves alone.
  const encodedPath = documentId.split("/").map(encodeURIComponent).join("/");
  const response = await fetch(`${API_BASE}/extractions/${encodedPath}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Failed to load extraction: ${response.status}`);
  return response.json();
}

export async function fetchMatches(status?: string): Promise<MatchOut[]> {
  const url = status ? `${API_BASE}/matches?status=${encodeURIComponent(status)}` : `${API_BASE}/matches`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Failed to load matches: ${response.status}`);
  return response.json();
}

export async function fetchMatchesForDocument(documentId: string): Promise<MatchOut[]> {
  const url = `${API_BASE}/matches?document_id=${encodeURIComponent(documentId)}`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Failed to load matches: ${response.status}`);
  return response.json();
}

export function getDocumentFileUrl(documentId: string, options?: { download?: boolean }): string {
  // document_id contains literal "/" - same {..:path} route pattern/encoding as fetchExtraction.
  const encodedPath = documentId.split("/").map(encodeURIComponent).join("/");
  const url = `${API_BASE}/documents/${encodedPath}/file`;
  return options?.download ? `${url}?download=true` : url;
}

export function getPublicAppendixFileUrl(companyId: string, appendixNumber: string): string {
  return `${PUBLIC_API_BASE}/companies/${encodeURIComponent(companyId)}/appendices/${encodeURIComponent(appendixNumber)}/file`;
}

export interface PublicAppendixFileResult {
  requestUrl: string;
  status: number;
  ok: boolean;
  contentType: string | null;
  contentLength: number | null;
  fileName: string | null;
  blobUrl: string | null;
  errorDetail: string | null;
}

/** Calls the real public appendix-lookup API (api/public_routes.py) exactly as
 * an external, unauthenticated caller would - not an internal dashboard route. */
export async function callPublicAppendixApi(
  companyId: string,
  appendixNumber: string
): Promise<PublicAppendixFileResult> {
  const requestUrl = getPublicAppendixFileUrl(companyId, appendixNumber);
  const response = await fetch(requestUrl);
  const contentType = response.headers.get("content-type");
  const contentDisposition = response.headers.get("content-disposition");
  // Non-ASCII original_file_name values (Hebrew is the common case here) make
  // Starlette emit RFC 5987 filename*=UTF-8''<percent-encoded> instead of a
  // plain filename="..." - check that form first, decode it, then fall back.
  const fileNameStarMatch = contentDisposition?.match(/filename\*=UTF-8''([^;]+)/i);
  const fileNameMatch = contentDisposition?.match(/filename="?([^";]+)"?/i);
  const fileName = fileNameStarMatch
    ? decodeURIComponent(fileNameStarMatch[1])
    : (fileNameMatch?.[1] ?? null);

  if (!response.ok) {
    let errorDetail: string | null = null;
    try {
      const body = (await response.json()) as { detail?: string };
      errorDetail = body.detail ?? null;
    } catch {
      // Error body wasn't JSON - leave errorDetail null, status code still shown.
    }
    return {
      requestUrl,
      status: response.status,
      ok: false,
      contentType,
      contentLength: null,
      fileName,
      blobUrl: null,
      errorDetail,
    };
  }

  const blob = await response.blob();
  return {
    requestUrl,
    status: response.status,
    ok: true,
    contentType,
    contentLength: blob.size,
    fileName,
    blobUrl: URL.createObjectURL(blob),
    errorDetail: null,
  };
}

export interface PublicAppendixMatch {
  company_id: string;
  appendix_number: string[];
  appendix_name: string | null;
  domain: string;
  similarity_score: number;
  status: string;
}

/** Calls the real public comparison endpoint - the cross-company appendices
 * this one was matched to, with a similarity score and review status. */
export async function fetchPublicAppendixMatches(
  companyId: string,
  appendixNumber: string
): Promise<PublicAppendixMatch[]> {
  const url = `${PUBLIC_API_BASE}/companies/${encodeURIComponent(companyId)}/appendices/${encodeURIComponent(appendixNumber)}/matches`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Failed to load matches: ${response.status}`);
  return response.json();
}

export async function updateMatchStatus(
  matchId: string,
  status: "confirmed" | "rejected"
): Promise<MatchOut> {
  // match_id is "{document_id}:{matched_document_id}", and document ids contain
  // literal "/" - same {..:path} route pattern/encoding as fetchExtraction.
  const encodedPath = matchId.split("/").map(encodeURIComponent).join("/");
  const response = await fetch(`${API_BASE}/matches/${encodedPath}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!response.ok) throw new Error(`Failed to update match: ${response.status}`);
  return response.json();
}
