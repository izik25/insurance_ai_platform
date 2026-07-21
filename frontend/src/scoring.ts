import type { ExtractionOut } from "./api";

/** Free, deterministic rule-of-thumb comparison - not a substitute for expert
 * judgment. Every criterion below is an approximation (e.g. "more diseases
 * covered = better" ignores which diseases); it exists to give a quick,
 * directional read, not a definitive verdict. */

export type Winner = "A" | "B" | "tie";

export interface CriterionScore {
  label: string;
  winner: Winner;
  detailA: string;
  detailB: string;
}

const DURATION_UNIT_DAYS: Record<string, number> = {
  יום: 1,
  ימים: 1,
  שבוע: 7,
  שבועות: 7,
  חודש: 30,
  חודשים: 30,
  שנה: 365,
  שנים: 365,
};

function parseMaxAmount(amounts: string[]): number | null {
  const numbers = amounts
    .flatMap((s) => s.match(/[\d,]{3,}/g) ?? [])
    .map((s) => parseInt(s.replace(/,/g, ""), 10))
    .filter((n) => !isNaN(n));
  return numbers.length ? Math.max(...numbers) : null;
}

function parseDurationDays(text: string | null): number | null {
  if (!text) return null;
  const match = text.match(/(\d+)\s*(יום|ימים|שבוע|שבועות|חודש|חודשים|שנה|שנים)/);
  if (!match) return null;
  const unit = DURATION_UNIT_DAYS[match[2]];
  return unit ? parseInt(match[1], 10) * unit : null;
}

function compareValues(
  label: string,
  a: number | null,
  b: number | null,
  higherIsBetter: boolean,
  format: (n: number) => string
): CriterionScore | null {
  if (a == null || b == null) return null;
  const winner: Winner = a === b ? "tie" : (higherIsBetter ? a > b : a < b) ? "A" : "B";
  return { label, winner, detailA: format(a), detailB: format(b) };
}

export function scoreComparison(a: ExtractionOut, b: ExtractionOut): CriterionScore[] {
  const results: (CriterionScore | null)[] = [
    compareValues(
      "סכום ביטוח מקסימלי שהוזכר",
      parseMaxAmount(a.insurance_amounts),
      parseMaxAmount(b.insurance_amounts),
      true,
      (n) => `₪${n.toLocaleString("he-IL")}`
    ),
    compareValues(
      "תקופת המתנה (קצרה יותר עדיפה)",
      parseDurationDays(a.waiting_period),
      parseDurationDays(b.waiting_period),
      false,
      (n) => `${n} ימים`
    ),
    compareValues(
      "תקופת אכשרה (קצרה יותר עדיפה)",
      parseDurationDays(a.qualifying_period),
      parseDurationDays(b.qualifying_period),
      false,
      (n) => `${n} ימים`
    ),
    compareValues(
      "תקופת הישרדות (קצרה יותר עדיפה)",
      parseDurationDays(a.survival_period),
      parseDurationDays(b.survival_period),
      false,
      (n) => `${n} ימים`
    ),
    compareValues(
      "מספר חריגים (פחות עדיף)",
      a.exclusions.length,
      b.exclusions.length,
      false,
      (n) => `${n}`
    ),
    compareValues(
      "מספר הגבלות (פחות עדיף)",
      a.restrictions.length,
      b.restrictions.length,
      false,
      (n) => `${n}`
    ),
    a.disease_count != null && b.disease_count != null
      ? compareValues(
          "מספר מחלות מכוסות (יותר עדיף)",
          a.disease_count,
          b.disease_count,
          true,
          (n) => `${n}`
        )
      : null,
  ];
  return results.filter((r): r is CriterionScore => r !== null);
}

export interface OverallScore {
  percentA: number;
  percentB: number;
  comparedCriteria: number;
}

export function overallScore(criteria: CriterionScore[]): OverallScore | null {
  if (criteria.length === 0) return null;
  const pointsA = criteria.filter((c) => c.winner === "A").length;
  const pointsB = criteria.filter((c) => c.winner === "B").length;
  const ties = criteria.filter((c) => c.winner === "tie").length;
  const total = pointsA + pointsB + ties;
  const percentA = ((pointsA + ties / 2) / total) * 100;
  return { percentA, percentB: 100 - percentA, comparedCriteria: total };
}
