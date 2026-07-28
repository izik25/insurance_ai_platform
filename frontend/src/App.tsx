import { useEffect, useMemo, useState } from "react";
import "./App.css";
import {
  callPublicAppendixApi,
  fetchDocuments,
  fetchExtraction,
  fetchMatches,
  fetchMatchesForDocument,
  fetchPublicAppendixMatches,
  getDocumentFileUrl,
  updateMatchStatus,
  type DocumentOut,
  type ExtractionOut,
  type MatchOut,
  type PublicAppendixFileResult,
  type PublicAppendixMatch,
} from "./api";
import { overallScore, scoreComparison } from "./scoring";

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="stat-card">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function DocumentsTable({
  documents,
  onSelect,
  selectedId,
}: {
  documents: DocumentOut[];
  onSelect: (id: string) => void;
  selectedId: string | null;
}) {
  return (
    <div className="table-wrap">
      <table className="fixed-table documents-table">
        <colgroup>
          <col style={{ width: "9%" }} />
          <col style={{ width: "7%" }} />
          <col style={{ width: "28%" }} />
          <col style={{ width: "10%" }} />
          <col style={{ width: "22%" }} />
          <col style={{ width: "10%" }} />
          <col style={{ width: "7%" }} />
          <col style={{ width: "7%" }} />
        </colgroup>
        <thead>
          <tr>
            <th>חברה</th>
            <th>תחום</th>
            <th>שם קובץ</th>
            <th>מספר נספח</th>
            <th>שם נספח</th>
            <th>שיטת חילוץ</th>
            <th>חילוץ</th>
            <th>Embedding</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((d) => (
            <tr
              key={d.id}
              className={d.id === selectedId ? "selected" : ""}
              onClick={() => {
                console.log("[DEBUG] document row clicked:", d.id);
                onSelect(d.id);
              }}
            >
              <td>{d.company_id}</td>
              <td>{d.domain}</td>
              <td className="mono truncate" title={d.original_file_name}>
                {d.original_file_name}
              </td>
              <td>{d.appendix_number.join(", ")}</td>
              <td className="truncate" title={d.appendix_name ?? ""}>
                {d.appendix_name ?? "—"}
              </td>
              <td>{d.extraction_method}</td>
              <td>{d.has_extraction ? "✓" : "—"}</td>
              <td>{d.has_embedding ? "✓" : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ListField({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="field-block">
      <span className="field-label">{label}</span>
      <ul>
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function StatusRow({
  document,
  matches,
}: {
  document: DocumentOut;
  matches: MatchOut[];
}) {
  const bestMatch = matches.length
    ? [...matches].sort((a, b) => b.similarity_score - a.similarity_score)[0]
    : null;
  return (
    <div className="field-grid status-grid">
      <div>
        <span className="field-label">שיטת חילוץ טקסט</span>
        <span>{document.extraction_method}</span>
      </div>
      <div>
        <span className="field-label">חילוץ שדות (LLM)</span>
        <span>{document.has_extraction ? "✓ בוצע" : "— טרם בוצע"}</span>
      </div>
      <div>
        <span className="field-label">Embedding</span>
        <span>{document.has_embedding ? "✓ בוצע" : "— טרם בוצע"}</span>
      </div>
      <div>
        <span className="field-label">התאמה חוצת-חברות</span>
        <span>
          {bestMatch
            ? `${(bestMatch.similarity_score * 100).toFixed(1)}% (${STATUS_LABELS[bestMatch.status] ?? bestMatch.status})`
            : "— אין עדיין"}
        </span>
      </div>
    </div>
  );
}

const STATUS_LABELS: Record<string, string> = {
  auto_confirmed: "מאושר אוטומטית",
  pending_review: "ממתין לבדיקה",
  confirmed: "אושר ידנית",
  rejected: "נדחה",
};

function DocumentMatchesTable({
  documentId,
  matches,
  onSelect,
}: {
  documentId: string;
  matches: MatchOut[];
  onSelect: (match: MatchOut) => void;
}) {
  if (matches.length === 0) {
    return <p className="muted">למסמך זה אין עדיין התאמה עם חברה אחרת.</p>;
  }
  return (
    <div className="table-wrap">
      <table className="fixed-table">
        <colgroup>
          <col style={{ width: "12%" }} />
          <col style={{ width: "28%" }} />
          <col style={{ width: "12%" }} />
          <col style={{ width: "28%" }} />
          <col style={{ width: "10%" }} />
          <col style={{ width: "10%" }} />
        </colgroup>
        <thead>
          <tr>
            <th>חברה מקבילה</th>
            <th>שם קובץ מקביל</th>
            <th>מספר נספח מקביל</th>
            <th>שם נספח מקביל</th>
            <th>אחוז דמיון</th>
            <th>סטטוס</th>
          </tr>
        </thead>
        <tbody>
          {matches.map((m) => {
            const other = m.document.id === documentId ? m.matched_document : m.document;
            return (
              <tr key={m.id} onClick={() => onSelect(m)}>
                <td>{other.company_id}</td>
                <td className="mono truncate" title={other.original_file_name}>
                  {other.original_file_name}
                </td>
                <td>{other.appendix_number.join(", ") || "—"}</td>
                <td className="truncate" title={other.appendix_name ?? ""}>
                  {other.appendix_name ?? "—"}
                </td>
                <td className={m.similarity_score >= 0.95 ? "score-high" : "score-low"}>
                  {(m.similarity_score * 100).toFixed(1)}%
                </td>
                <td>{STATUS_LABELS[m.status] ?? m.status}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ExtractionPanel({ extraction }: { extraction: ExtractionOut | null }) {
  if (!extraction) {
    return <p className="muted">למסמך זה עדיין אין חילוץ שדות (טרם עבר עיבוד LLM).</p>;
  }
  return (
    <div className="extraction-panel">
      <div className="field-grid">
        <div>
          <span className="field-label">מספר נספח</span>
          <span>{extraction.appendix_number.join(", ") || "—"}</span>
        </div>
        <div>
          <span className="field-label">שם נספח</span>
          <span>{extraction.appendix_name ?? "—"}</span>
        </div>
        <div>
          <span className="field-label">סוג כיסוי</span>
          <span>{extraction.coverage_type ?? "—"}</span>
        </div>
        <div>
          <span className="field-label">שם כיסוי</span>
          <span>{extraction.coverage_name ?? "—"}</span>
        </div>
        <div>
          <span className="field-label">תקופת אכשרה</span>
          <span>{extraction.qualifying_period ?? "—"}</span>
        </div>
        <div>
          <span className="field-label">תקופת המתנה</span>
          <span>{extraction.waiting_period ?? "—"}</span>
        </div>
        <div>
          <span className="field-label">טווח גילאים</span>
          <span>{extraction.age_range ?? "—"}</span>
        </div>
        <div>
          <span className="field-label">תקופת הישרדות</span>
          <span>{extraction.survival_period ?? "—"}</span>
        </div>
      </div>
      <div className="field-block">
        <span className="field-label">תנאי זכאות</span>
        <p>{extraction.eligibility_conditions ?? "—"}</p>
      </div>
      <ListField label="סכומי ביטוח" items={extraction.insurance_amounts} />
      <ListField label="חריגים" items={extraction.exclusions} />
      <ListField label="הגבלות" items={extraction.restrictions} />
      <ListField
        label={`רשימת מחלות${extraction.disease_count != null ? ` (${extraction.disease_count})` : ""}`}
        items={extraction.disease_list}
      />
      {extraction.tables.map((table, index) => (
        <div className="field-block" key={index}>
          <span className="field-label">{table.title ?? `טבלה ${index + 1}`}</span>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {table.headers.map((header, i) => (
                    <th key={i}>{header}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <td key={j}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

function MatchesTable({
  matches,
  onSelect,
}: {
  matches: MatchOut[];
  onSelect: (match: MatchOut) => void;
}) {
  if (matches.length === 0) return <p className="muted">אין התאמות בקטגוריה זו.</p>;
  return (
    <div className="table-wrap">
      <table className="fixed-table">
        <colgroup>
          <col style={{ width: "10%" }} />
          <col style={{ width: "33%" }} />
          <col style={{ width: "10%" }} />
          <col style={{ width: "33%" }} />
          <col style={{ width: "7%" }} />
          <col style={{ width: "7%" }} />
        </colgroup>
        <thead>
          <tr>
            <th>חברה א׳</th>
            <th>מסמך א׳</th>
            <th>חברה ב׳</th>
            <th>מסמך ב׳</th>
            <th>אחוז דמיון</th>
            <th>סטטוס</th>
          </tr>
        </thead>
        <tbody>
          {matches.map((m) => (
            <tr key={m.id} onClick={() => onSelect(m)}>
              <td>{m.document.company_id}</td>
              <td className="truncate" title={m.document.original_file_name}>
                {m.document.original_file_name}
                <div className="muted small">{m.document.appendix_name ?? ""}</div>
              </td>
              <td>{m.matched_document.company_id}</td>
              <td className="truncate" title={m.matched_document.original_file_name}>
                {m.matched_document.original_file_name}
                <div className="muted small">{m.matched_document.appendix_name ?? ""}</div>
              </td>
              <td className={m.similarity_score >= 0.95 ? "score-high" : "score-low"}>
                {(m.similarity_score * 100).toFixed(1)}%
              </td>
              <td>{STATUS_LABELS[m.status] ?? m.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DocumentDetailModal({
  document,
  onClose,
  onSelectMatch,
}: {
  document: DocumentOut;
  onClose: () => void;
  onSelectMatch: (match: MatchOut) => void;
}) {
  const [extraction, setExtraction] = useState<ExtractionOut | null>(null);
  const [matches, setMatches] = useState<MatchOut[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchExtraction(document.id), fetchMatchesForDocument(document.id)])
      .then(([ext, m]) => {
        setExtraction(ext);
        setMatches(m);
      })
      .finally(() => setLoading(false));
  }, [document.id]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>
            {document.company_id} — {document.original_file_name}
          </h2>
          <button className="modal-close" onClick={onClose} aria-label="סגור">
            ✕
          </button>
        </div>

        <div className="source-file-actions">
          <a
            className="source-file-link"
            href={getDocumentFileUrl(document.id)}
            target="_blank"
            rel="noopener noreferrer"
          >
            📄 צפייה בקובץ המקור
          </a>
          <a
            className="source-file-link source-file-link-secondary"
            href={getDocumentFileUrl(document.id, { download: true })}
          >
            ⬇ הורדת קובץ המקור
          </a>
        </div>

        {loading && <p className="muted">טוען פרטי מסמך...</p>}

        {!loading && (
          <>
            <h3>סטטוס עיבוד</h3>
            <StatusRow document={document} matches={matches} />
            <h3>שדות שחולצו</h3>
            <ExtractionPanel extraction={extraction} />
            <h3>התאמות למסמך זה</h3>
            <DocumentMatchesTable documentId={document.id} matches={matches} onSelect={onSelectMatch} />
          </>
        )}
      </div>
    </div>
  );
}

function documentLabel(d: DocumentOut): string {
  const appendix = d.appendix_number.join(", ");
  const parts = [d.original_file_name, appendix && `נספח ${appendix}`, d.appendix_name].filter(Boolean);
  return parts.join(" — ");
}

function ComparisonPickerModal({
  documents,
  onClose,
  onFoundMatch,
  onFoundMultiple,
}: {
  documents: DocumentOut[];
  onClose: () => void;
  onFoundMatch: (match: MatchOut) => void;
  onFoundMultiple: (documentAId: string, matches: MatchOut[], missingCompanies: string[]) => void;
}) {
  const companies = useMemo(
    () => Array.from(new Set(documents.map((d) => d.company_id))).sort(),
    [documents]
  );
  const [companyA, setCompanyA] = useState("");
  const [documentSearch, setDocumentSearch] = useState("");
  const [documentAId, setDocumentAId] = useState("");
  const [companiesB, setCompaniesB] = useState<string[]>([]);
  const [searching, setSearching] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [otherMatches, setOtherMatches] = useState<MatchOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  const documentsForA = useMemo(
    () => (companyA ? documents.filter((d) => d.company_id === companyA) : []),
    [documents, companyA]
  );
  const otherCompanies = useMemo(
    () => companies.filter((c) => c !== companyA),
    [companies, companyA]
  );

  const filteredDocumentsForA = useMemo(() => {
    const term = documentSearch.trim().toLowerCase();
    const pool = term
      ? documentsForA.filter(
          (d) =>
            d.original_file_name.toLowerCase().includes(term) ||
            d.appendix_number.some((n) => n.toLowerCase().includes(term)) ||
            (d.appendix_name ?? "").toLowerCase().includes(term)
        )
      : documentsForA;
    return pool.slice(0, 200);
  }, [documentsForA, documentSearch]);

  const toggleCompanyB = (company: string) => {
    setCompaniesB((prev) =>
      prev.includes(company) ? prev.filter((c) => c !== company) : [...prev, company]
    );
    setNotFound(false);
  };

  const canSearch = Boolean(companyA && documentAId && companiesB.length > 0);

  const runComparison = () => {
    if (!canSearch) return;
    setSearching(true);
    setNotFound(false);
    setOtherMatches([]);
    setError(null);
    fetchMatchesForDocument(documentAId)
      .then((matches) => {
        const foundByCompany = new Map<string, MatchOut>();
        for (const company of companiesB) {
          const found = matches.find(
            (m) =>
              (m.document.id === documentAId && m.matched_document.company_id === company) ||
              (m.matched_document.id === documentAId && m.document.company_id === company)
          );
          if (found) foundByCompany.set(company, found);
        }
        const foundMatches = Array.from(foundByCompany.values());
        const missingCompanies = companiesB.filter((c) => !foundByCompany.has(c));

        if (companiesB.length === 1) {
          if (foundMatches.length === 1) {
            onFoundMatch(foundMatches[0]);
          } else {
            setNotFound(true);
            setOtherMatches(matches);
          }
          return;
        }

        if (foundMatches.length === 0) {
          setNotFound(true);
          setOtherMatches(matches);
          return;
        }

        onFoundMultiple(documentAId, foundMatches, missingCompanies);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setSearching(false));
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>השוואת נספחים בין חברות</h2>
          <button className="modal-close" onClick={onClose} aria-label="סגור">
            ✕
          </button>
        </div>

        <div className="picker-form">
          <div className="picker-field">
            <label>חברת ביטוח א׳</label>
            <select
              value={companyA}
              onChange={(e) => {
                setCompanyA(e.target.value);
                setDocumentAId("");
                setDocumentSearch("");
                setCompaniesB([]);
                setNotFound(false);
              }}
            >
              <option value="">בחר חברה...</option>
              {companies.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>

          {companyA && (
            <div className="picker-field">
              <label>נספח / מסמך ({documentsForA.length} מסמכים)</label>
              <input
                type="text"
                placeholder="חפש לפי שם קובץ או מספר נספח..."
                value={documentSearch}
                onChange={(e) => setDocumentSearch(e.target.value)}
              />
              <select
                value={documentAId}
                onChange={(e) => {
                  setDocumentAId(e.target.value);
                  setNotFound(false);
                }}
                size={Math.min(8, Math.max(4, filteredDocumentsForA.length))}
              >
                <option value="">בחר נספח...</option>
                {filteredDocumentsForA.map((d) => (
                  <option key={d.id} value={d.id}>
                    {documentLabel(d)}
                  </option>
                ))}
              </select>
              {filteredDocumentsForA.length === 200 && (
                <p className="muted small">מציג 200 תוצאות ראשונות - צמצם את החיפוש אם לא מוצא.</p>
              )}
            </div>
          )}

          {documentAId && (
            <div className="picker-field">
              <label>השווה מול חברות (אפשר לבחור כמה)</label>
              <div className="checkbox-list">
                {otherCompanies.map((c) => (
                  <label key={c} className="checkbox-item">
                    <input
                      type="checkbox"
                      checked={companiesB.includes(c)}
                      onChange={() => toggleCompanyB(c)}
                    />
                    {c}
                  </label>
                ))}
              </div>
            </div>
          )}

          <button className="confirm-button" disabled={!canSearch || searching} onClick={runComparison}>
            {searching ? "בודק..." : "בדוק השוואה"}
          </button>

          {error && <p className="error">שגיאה: {error}</p>}

          {notFound && (
            <div className="picker-not-found">
              <p className="error">
                אין מיפוי קיים בין המסמך שנבחר לבין{" "}
                {companiesB.join(", ")}. ייתכן שהמסמך הזה עדיין לא הותאם לאף מסמך באחת מהחברות האלה.
              </p>
              {otherMatches.length > 0 && (
                <>
                  <p className="muted">אבל נמצאו התאמות למסמך זה מול חברות אחרות:</p>
                  <DocumentMatchesTable
                    documentId={documentAId}
                    matches={otherMatches}
                    onSelect={onFoundMatch}
                  />
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ScoreSummary({
  extractionA,
  extractionB,
  labelA,
  labelB,
}: {
  extractionA: ExtractionOut;
  extractionB: ExtractionOut;
  labelA: string;
  labelB: string;
}) {
  const criteria = useMemo(() => scoreComparison(extractionA, extractionB), [extractionA, extractionB]);
  const overall = useMemo(() => overallScore(criteria), [criteria]);

  return (
    <div className="score-summary">
      <p className="muted small">
        השוואה לפי כלל אצבע פשוט וחינמי (סכום ביטוח, תקופות המתנה/אכשרה, מספר חריגים/הגבלות
        וכו') - לא ייעוץ מקצועי, רק כיוון מהיר.
      </p>
      {overall === null ? (
        <p className="muted">אין מספיק נתונים מספריים בשני הצדדים כדי להשוות.</p>
      ) : (
        <>
          <div className="score-bar">
            <div className="score-bar-a" style={{ width: `${overall.percentA}%` }}>
              {overall.percentA >= 15 && `${overall.percentA.toFixed(0)}%`}
            </div>
            <div className="score-bar-b" style={{ width: `${overall.percentB}%` }}>
              {overall.percentB >= 15 && `${overall.percentB.toFixed(0)}%`}
            </div>
          </div>
          <div className="score-bar-labels">
            <span>
              {labelA}: {overall.percentA.toFixed(0)}%
            </span>
            <span>
              {labelB}: {overall.percentB.toFixed(0)}%
            </span>
          </div>
          <table className="score-table">
            <thead>
              <tr>
                <th>קריטריון</th>
                <th>{labelA}</th>
                <th>{labelB}</th>
                <th>עדיף</th>
              </tr>
            </thead>
            <tbody>
              {criteria.map((c) => (
                <tr key={c.label}>
                  <td>{c.label}</td>
                  <td>{c.detailA}</td>
                  <td>{c.detailB}</td>
                  <td className={c.winner === "tie" ? "" : "score-high"}>
                    {c.winner === "A" ? `◄ ${labelA}` : c.winner === "B" ? `${labelB} ►` : "תיקו"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function MatchReviewModal({
  match,
  onClose,
  onResolved,
}: {
  match: MatchOut;
  onClose: () => void;
  onResolved: (updated: MatchOut) => void;
}) {
  const [extractionA, setExtractionA] = useState<ExtractionOut | null>(null);
  const [extractionB, setExtractionB] = useState<ExtractionOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState(match.status);
  const [saving, setSaving] = useState<"confirmed" | "rejected" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchExtraction(match.document.id), fetchExtraction(match.matched_document.id)])
      .then(([a, b]) => {
        setExtractionA(a);
        setExtractionB(b);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [match.document.id, match.matched_document.id]);

  const decide = (nextStatus: "confirmed" | "rejected") => {
    setSaving(nextStatus);
    setError(null);
    updateMatchStatus(match.id, nextStatus)
      .then((updated) => {
        setStatus(updated.status);
        onResolved(updated);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setSaving(null));
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>
            השוואת התאמה — {(match.similarity_score * 100).toFixed(1)}% דמיון (
            {STATUS_LABELS[status] ?? status})
          </h2>
          <button className="modal-close" onClick={onClose} aria-label="סגור">
            ✕
          </button>
        </div>

        {error && <p className="error">שגיאה: {error}</p>}

        <div className="modal-actions">
          <button
            className="confirm-button"
            disabled={saving !== null || status === "confirmed"}
            onClick={() => decide("confirmed")}
          >
            {saving === "confirmed" ? "מאשר..." : "✓ אשר התאמה"}
          </button>
          <button
            className="reject-button"
            disabled={saving !== null || status === "rejected"}
            onClick={() => decide("rejected")}
          >
            {saving === "rejected" ? "דוחה..." : "✕ דחה התאמה"}
          </button>
        </div>

        {loading && <p className="muted">טוען את שני המסמכים...</p>}

        {!loading && extractionA && extractionB && (
          <>
            <h3>מי עדיף?</h3>
            <ScoreSummary
              extractionA={extractionA}
              extractionB={extractionB}
              labelA={match.document.company_id}
              labelB={match.matched_document.company_id}
            />
          </>
        )}

        {!loading && (
          <div className="comparison-grid">
            <div className="comparison-side">
              <h3>
                {match.document.company_id} — {match.document.original_file_name}
              </h3>
              <p className="muted small">
                נספח {match.document.appendix_number.join(", ") || "—"}
                {match.document.appendix_name ? ` · ${match.document.appendix_name}` : ""}
              </p>
              <ExtractionPanel extraction={extractionA} />
            </div>
            <div className="comparison-side">
              <h3>
                {match.matched_document.company_id} — {match.matched_document.original_file_name}
              </h3>
              <p className="muted small">
                נספח {match.matched_document.appendix_number.join(", ") || "—"}
                {match.matched_document.appendix_name ? ` · ${match.matched_document.appendix_name}` : ""}
              </p>
              <ExtractionPanel extraction={extractionB} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MultiComparisonModal({
  documentAId,
  matches,
  missingCompanies,
  onClose,
}: {
  documentAId: string;
  matches: MatchOut[];
  missingCompanies: string[];
  onClose: () => void;
}) {
  const [extractions, setExtractions] = useState<Record<string, ExtractionOut | null>>({});
  const [loading, setLoading] = useState(true);

  // Every match has documentAId on one side - pull its summary from whichever
  // match has it, so the primary column's header doesn't need a separate fetch.
  const primarySummary =
    matches[0].document.id === documentAId ? matches[0].document : matches[0].matched_document;
  const otherSides = matches.map((m) =>
    m.document.id === documentAId
      ? { summary: m.matched_document, match: m }
      : { summary: m.document, match: m }
  );

  useEffect(() => {
    setLoading(true);
    const ids = [documentAId, ...otherSides.map((s) => s.summary.id)];
    Promise.all(ids.map((id) => fetchExtraction(id)))
      .then((results) => {
        const byId: Record<string, ExtractionOut | null> = {};
        ids.forEach((id, i) => (byId[id] = results[i]));
        setExtractions(byId);
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentAId, matches]);

  const primaryExtraction = extractions[documentAId] ?? null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box modal-box-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>
            השוואת נספחים — {primarySummary.company_id}/{primarySummary.original_file_name} מול{" "}
            {otherSides.length} חברות
          </h2>
          <button className="modal-close" onClick={onClose} aria-label="סגור">
            ✕
          </button>
        </div>

        {missingCompanies.length > 0 && (
          <p className="muted">
            לא נמצאה התאמה קיימת מול: {missingCompanies.join(", ")}.
          </p>
        )}

        {loading && <p className="muted">טוען את כל המסמכים...</p>}

        {!loading && (
          <div className="multi-comparison-grid">
            <div className="comparison-side">
              <h3>
                {primarySummary.company_id} — {primarySummary.original_file_name}
              </h3>
              <ExtractionPanel extraction={primaryExtraction} />
            </div>
            {otherSides.map(({ summary, match }) => (
              <div className="comparison-side" key={summary.id}>
                <h3>
                  {summary.company_id} — {summary.original_file_name}
                </h3>
                <p className="muted small">
                  {(match.similarity_score * 100).toFixed(1)}% דמיון (
                  {STATUS_LABELS[match.status] ?? match.status})
                </p>
                {primaryExtraction && extractions[summary.id] && (
                  <ScoreSummary
                    extractionA={primaryExtraction}
                    extractionB={extractions[summary.id] as ExtractionOut}
                    labelA={primarySummary.company_id}
                    labelB={summary.company_id}
                  />
                )}
                <ExtractionPanel extraction={extractions[summary.id] ?? null} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

interface AppendixLookupResult {
  appendixNumber: string;
  fileResult: PublicAppendixFileResult;
  // Only fetched when fileResult.ok (no document => nothing to compare).
  // null = not applicable/not attempted, undefined-free by design.
  matches: PublicAppendixMatch[] | null;
  matchesError: string | null;
}

/** Splits "205, 310,, 205" into ["205", "310"] - trims, drops empties, dedupes
 * while preserving input order (so the results list matches what was typed). */
function parseAppendixNumbers(input: string): string[] {
  const seen = new Set<string>();
  const numbers: string[] = [];
  for (const raw of input.split(",")) {
    const n = raw.trim();
    if (n && !seen.has(n)) {
      seen.add(n);
      numbers.push(n);
    }
  }
  return numbers;
}

function PublicApiDemoModal({
  documents,
  onClose,
}: {
  documents: DocumentOut[];
  onClose: () => void;
}) {
  const companies = useMemo(
    () => Array.from(new Set(documents.map((d) => d.company_id))).sort(),
    [documents]
  );
  const [companyId, setCompanyId] = useState("");
  const [appendixInput, setAppendixInput] = useState("");
  const [calling, setCalling] = useState(false);
  const [results, setResults] = useState<AppendixLookupResult[] | null>(null);

  const knownAppendixNumbers = useMemo(() => {
    if (!companyId) return [];
    const numbers = new Set<string>();
    for (const d of documents) {
      if (d.company_id !== companyId) continue;
      for (const n of d.appendix_number) numbers.add(n);
    }
    return Array.from(numbers).sort();
  }, [documents, companyId]);

  const parsedNumbers = useMemo(() => parseAppendixNumbers(appendixInput), [appendixInput]);

  // Blob URLs created for downloaded files must be revoked when they're
  // replaced or the modal closes, or they leak memory for the tab's lifetime.
  useEffect(() => {
    return () => {
      results?.forEach(({ fileResult }) => {
        if (fileResult.blobUrl) URL.revokeObjectURL(fileResult.blobUrl);
      });
    };
  }, [results]);

  const canCall = Boolean(companyId && parsedNumbers.length > 0);

  const runCall = () => {
    if (!canCall) return;
    setCalling(true);
    setResults(null);
    // Each number is looked up with its own independent request against the
    // real single-appendix endpoint - a 404 on one number doesn't affect the
    // others, it's just reported next to that number's result. When the
    // appendix is found, a second real call fetches its cross-company
    // comparison (/matches) so the demo shows the actual comparison feature,
    // not just the file lookup.
    Promise.all(
      parsedNumbers.map(async (appendixNumber) => {
        const fileResult = await callPublicAppendixApi(companyId, appendixNumber);
        let matches: PublicAppendixMatch[] | null = null;
        let matchesError: string | null = null;
        if (fileResult.ok) {
          try {
            matches = await fetchPublicAppendixMatches(companyId, appendixNumber);
          } catch (err) {
            matchesError = err instanceof Error ? err.message : "שגיאה בטעינת ההשוואה";
          }
        }
        return { appendixNumber, fileResult, matches, matchesError };
      })
    )
      .then(setResults)
      .finally(() => setCalling(false));
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>הדגמה חיה — API פתוח לשליפת נספח לפי מספר</h2>
          <button className="modal-close" onClick={onClose} aria-label="סגור">
            ✕
          </button>
        </div>

        <p className="muted small">
          זו קריאת HTTP אמיתית לשרת - בדיוק כפי שצד שלישי חיצוני היה מבצע מול ה-API הפתוח
          (ללא אימות, לא ה-API הפנימי של הדשבורד): בוחרים חברה ומספר נספח אחד או כמה (מופרדים
          בפסיק), והשרת מתבצע חיפוש בנפרד לכל מספר לפי <span className="mono">company_id</span> +{" "}
          <span className="mono">appendix_number</span> ומחזיר את קובץ המקור. אם מספר מסוים לא
          נמצא, זה מוצג כשגיאה ליד אותו מספר בלבד - שאר המספרים מוצגים כרגיל. עבור כל נספח שנמצא,
          נשלחת גם קריאה אמיתית שנייה ל-<span className="mono">/matches</span> - ההשוואה
          חוצת-החברות שהמערכת כבר חישבה לנספח הזה.
        </p>

        <div className="picker-form">
          <div className="picker-field">
            <label>חברת ביטוח (company_id)</label>
            <select
              value={companyId}
              onChange={(e) => {
                setCompanyId(e.target.value);
                setAppendixInput("");
                setResults(null);
              }}
            >
              <option value="">בחר חברה...</option>
              {companies.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>

          {companyId && (
            <div className="picker-field">
              <label>מספרי נספח (אפשר כמה, מופרדים בפסיק)</label>
              <input
                type="text"
                list="public-api-demo-appendix-suggestions"
                placeholder="לדוגמה: 205, 310, 12"
                value={appendixInput}
                onChange={(e) => {
                  setAppendixInput(e.target.value);
                  setResults(null);
                }}
              />
              <datalist id="public-api-demo-appendix-suggestions">
                {knownAppendixNumbers.map((n) => (
                  <option key={n} value={n} />
                ))}
              </datalist>
              {knownAppendixNumbers.length > 0 && (
                <p className="muted small">
                  {knownAppendixNumbers.length} מספרי נספח ידועים לחברה זו (רשימת עזר בלבד - אפשר
                  להקליד כל מספר, כולל כזה שלא קיים, כדי לבדוק גם תגובת שגיאה, ואפשר להקליד כמה
                  מספרים מופרדים בפסיק).
                </p>
              )}
            </div>
          )}

          {companyId && parsedNumbers.length > 0 && (
            <div className="field-block">
              <span className="field-label">
                {parsedNumbers.length > 1
                  ? `בקשות HTTP שיישלחו (${parsedNumbers.length})`
                  : "בקשת HTTP שתישלח"}
              </span>
              {parsedNumbers.map((n) => (
                <p className="mono small" key={n}>
                  GET {getPublicApiRequestPreview(companyId, n)}
                </p>
              ))}
            </div>
          )}

          <button className="confirm-button" disabled={!canCall || calling} onClick={runCall}>
            {calling ? "שולח בקשות..." : "בצע קריאה אמיתית ל-API"}
          </button>

          {results && (
            <div className="api-result-list">
              {results.map(({ appendixNumber, fileResult, matches, matchesError }) => (
                <div
                  key={appendixNumber}
                  className={
                    fileResult.ok ? "api-result api-result-success" : "api-result api-result-error"
                  }
                >
                  {parsedNumbers.length > 1 && <p className="api-result-title">נספח {appendixNumber}</p>}
                  <p>
                    <strong>סטטוס תגובה:</strong> {fileResult.status} {fileResult.ok ? "✓" : "✕"}
                  </p>
                  {fileResult.ok ? (
                    <>
                      <p>
                        <strong>Content-Type:</strong> {fileResult.contentType ?? "—"}
                      </p>
                      <p>
                        <strong>גודל:</strong>{" "}
                        {fileResult.contentLength != null
                          ? `${(fileResult.contentLength / 1024).toFixed(1)} KB`
                          : "—"}
                      </p>
                      <p>
                        <strong>שם קובץ:</strong> {fileResult.fileName ?? "—"}
                      </p>
                      <div className="source-file-actions">
                        <a
                          className="source-file-link"
                          href={fileResult.blobUrl ?? undefined}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          📄 צפייה בנספח שהתקבל
                        </a>
                        <a
                          className="source-file-link source-file-link-secondary"
                          href={fileResult.blobUrl ?? undefined}
                          download={fileResult.fileName ?? "appendix"}
                        >
                          ⬇ הורדה
                        </a>
                      </div>
                      <div className="api-result-matches">
                        <p className="field-label">
                          GET /public/v1/companies/{companyId}/appendices/{appendixNumber}/matches
                        </p>
                        {matchesError && <p className="error">{matchesError}</p>}
                        {!matchesError && matches && matches.length === 0 && (
                          <p className="muted small">
                            אין עדיין השוואה עבור נספח זה מול חברות אחרות.
                          </p>
                        )}
                        {!matchesError && matches && matches.length > 0 && (
                          <div className="table-wrap">
                            <table>
                              <thead>
                                <tr>
                                  <th>חברה מקבילה</th>
                                  <th>מספר נספח</th>
                                  <th>שם נספח</th>
                                  <th>דמיון</th>
                                  <th>סטטוס</th>
                                </tr>
                              </thead>
                              <tbody>
                                {matches.map((m) => (
                                  <tr key={`${m.company_id}:${m.appendix_number.join(",")}`}>
                                    <td>{m.company_id}</td>
                                    <td>{m.appendix_number.join(", ") || "—"}</td>
                                    <td className="truncate" title={m.appendix_name ?? ""}>
                                      {m.appendix_name ?? "—"}
                                    </td>
                                    <td className={m.similarity_score >= 0.95 ? "score-high" : "score-low"}>
                                      {(m.similarity_score * 100).toFixed(1)}%
                                    </td>
                                    <td>{STATUS_LABELS[m.status] ?? m.status}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    </>
                  ) : (
                    <p className="error">{fileResult.errorDetail ?? "הבקשה נכשלה."}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Preview-only string - not used for the actual fetch (that's built inside
// callPublicAppendixApi), just so the UI can show the exact URL beforehand.
function getPublicApiRequestPreview(companyId: string, appendixNumber: string): string {
  return `/public/v1/companies/${encodeURIComponent(companyId)}/appendices/${encodeURIComponent(appendixNumber)}/file`;
}

function App() {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [autoConfirmed, setAutoConfirmed] = useState<MatchOut[]>([]);
  const [pendingReview, setPendingReview] = useState<MatchOut[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showComparisonPicker, setShowComparisonPicker] = useState(false);
  const [showPublicApiDemo, setShowPublicApiDemo] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reviewingMatch, setReviewingMatch] = useState<MatchOut | null>(null);
  const [multiComparison, setMultiComparison] = useState<{
    documentAId: string;
    matches: MatchOut[];
    missingCompanies: string[];
  } | null>(null);

  useEffect(() => {
    Promise.all([
      fetchDocuments(),
      fetchMatches("auto_confirmed"),
      fetchMatches("pending_review"),
    ])
      .then(([docs, autoM, pendingM]) => {
        setDocuments(docs);
        setAutoConfirmed(autoM);
        setPendingReview(pendingM);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const refreshMatchLists = () => {
    Promise.all([fetchMatches("auto_confirmed"), fetchMatches("pending_review")]).then(
      ([autoM, pendingM]) => {
        setAutoConfirmed(autoM);
        setPendingReview(pendingM);
      }
    );
  };

  const selectedDocument = useMemo(
    () => documents.find((d) => d.id === selectedId) ?? null,
    [documents, selectedId]
  );

  const stats = useMemo(() => {
    const byCompany = new Map<string, number>();
    for (const doc of documents) {
      byCompany.set(doc.company_id, (byCompany.get(doc.company_id) ?? 0) + 1);
    }
    return {
      total: documents.length,
      extracted: documents.filter((d) => d.has_extraction).length,
      embedded: documents.filter((d) => d.has_embedding).length,
      byCompany,
    };
  }, [documents]);

  return (
    <div className="app" dir="rtl">
      <header className="app-header">
        <h1>לוח בקרה — פלטפורמת עיבוד מסמכי ביטוח</h1>
        <div className="header-actions">
          <button className="compare-button" onClick={() => setShowComparisonPicker(true)}>
            השוואה
          </button>
          <button className="compare-button" onClick={() => setShowPublicApiDemo(true)}>
            הדגמת API פתוח
          </button>
        </div>
      </header>

      {loading && <p className="muted">טוען נתונים...</p>}
      {error && <p className="error">שגיאה בטעינת נתונים: {error}</p>}

      {!loading && !error && (
        <>
          <section className="stats-row">
            <StatCard label="סה״כ מסמכים" value={stats.total} />
            <StatCard label="חולצו" value={stats.extracted} />
            <StatCard label="Embeddings" value={stats.embedded} />
            <StatCard label="התאמות מאושרות" value={autoConfirmed.length} />
            <StatCard label="ממתינות לבדיקה" value={pendingReview.length} />
            {[...stats.byCompany.entries()].map(([company, count]) => (
              <StatCard key={company} label={company} value={count} />
            ))}
          </section>

          <section>
            <h2>מסמכים שהורדו</h2>
            <DocumentsTable documents={documents} onSelect={setSelectedId} selectedId={selectedId} />
          </section>

          <section>
            <h2>התאמות שאושרו אוטומטית (≥95%)</h2>
            <MatchesTable matches={autoConfirmed} onSelect={setReviewingMatch} />
          </section>

          <section>
            <h2>ממתינות לבדיקה ידנית</h2>
            <MatchesTable matches={pendingReview} onSelect={setReviewingMatch} />
          </section>
        </>
      )}

      {selectedDocument && !reviewingMatch && (
        <DocumentDetailModal
          document={selectedDocument}
          onClose={() => setSelectedId(null)}
          onSelectMatch={(match) => {
            setSelectedId(null);
            setReviewingMatch(match);
          }}
        />
      )}

      {reviewingMatch && (
        <MatchReviewModal
          match={reviewingMatch}
          onClose={() => setReviewingMatch(null)}
          onResolved={() => {
            refreshMatchLists();
            setReviewingMatch(null);
          }}
        />
      )}

      {showComparisonPicker && !reviewingMatch && !multiComparison && (
        <ComparisonPickerModal
          documents={documents}
          onClose={() => setShowComparisonPicker(false)}
          onFoundMatch={(match) => {
            setShowComparisonPicker(false);
            setReviewingMatch(match);
          }}
          onFoundMultiple={(documentAId, matches, missingCompanies) => {
            setShowComparisonPicker(false);
            setMultiComparison({ documentAId, matches, missingCompanies });
          }}
        />
      )}

      {multiComparison && (
        <MultiComparisonModal
          documentAId={multiComparison.documentAId}
          matches={multiComparison.matches}
          missingCompanies={multiComparison.missingCompanies}
          onClose={() => setMultiComparison(null)}
        />
      )}

      {showPublicApiDemo && (
        <PublicApiDemoModal documents={documents} onClose={() => setShowPublicApiDemo(false)} />
      )}
    </div>
  );
}

export default App;
