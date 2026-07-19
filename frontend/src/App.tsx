import { useEffect, useMemo, useState } from "react";
import "./App.css";
import {
  fetchDocuments,
  fetchExtraction,
  fetchMatches,
  type DocumentOut,
  type ExtractionOut,
  type MatchOut,
} from "./api";

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
      <table>
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
              onClick={() => onSelect(d.id)}
            >
              <td>{d.company_id}</td>
              <td>{d.domain}</td>
              <td className="mono">{d.original_file_name}</td>
              <td>{d.appendix_number.join(", ")}</td>
              <td>{d.appendix_name ?? "—"}</td>
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

function ExtractionPanel({ extraction }: { extraction: ExtractionOut | null }) {
  if (!extraction) {
    return <p className="muted">בחר מסמך עם חילוץ כדי לראות את השדות שחולצו.</p>;
  }
  return (
    <div className="extraction-panel">
      <div className="field-grid">
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

function MatchesTable({ matches }: { matches: MatchOut[] }) {
  if (matches.length === 0) return <p className="muted">אין התאמות בקטגוריה זו.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>חברה א׳</th>
            <th>מסמך א׳</th>
            <th>חברה ב׳</th>
            <th>מסמך ב׳</th>
            <th>אחוז דמיון</th>
          </tr>
        </thead>
        <tbody>
          {matches.map((m) => (
            <tr key={m.id}>
              <td>{m.document.company_id}</td>
              <td>
                {m.document.original_file_name}
                <div className="muted small">{m.document.appendix_name ?? ""}</div>
              </td>
              <td>{m.matched_document.company_id}</td>
              <td>
                {m.matched_document.original_file_name}
                <div className="muted small">{m.matched_document.appendix_name ?? ""}</div>
              </td>
              <td className={m.similarity_score >= 0.95 ? "score-high" : "score-low"}>
                {(m.similarity_score * 100).toFixed(1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function App() {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [autoConfirmed, setAutoConfirmed] = useState<MatchOut[]>([]);
  const [pendingReview, setPendingReview] = useState<MatchOut[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedExtraction, setSelectedExtraction] = useState<ExtractionOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => {
    if (!selectedId) {
      setSelectedExtraction(null);
      return;
    }
    fetchExtraction(selectedId)
      .then(setSelectedExtraction)
      .catch(() => setSelectedExtraction(null));
  }, [selectedId]);

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
        <button className="compare-button" disabled title="בקרוב">
          השוואה
        </button>
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
            <DocumentsTable
              documents={documents}
              onSelect={setSelectedId}
              selectedId={selectedId}
            />
          </section>

          <section>
            <h2>חילוץ שדות{selectedId ? "" : " — בחר מסמך למעלה"}</h2>
            <ExtractionPanel extraction={selectedExtraction} />
          </section>

          <section>
            <h2>התאמות שאושרו אוטומטית (≥95%)</h2>
            <MatchesTable matches={autoConfirmed} />
          </section>

          <section>
            <h2>ממתינות לבדיקה ידנית</h2>
            <MatchesTable matches={pendingReview} />
          </section>
        </>
      )}
    </div>
  );
}

export default App;
