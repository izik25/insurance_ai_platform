const API_BASE = "http://127.0.0.1:8000/api";

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
  created_date: string;
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
