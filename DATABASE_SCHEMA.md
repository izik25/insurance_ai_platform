# מבנה הנתונים והזיכרון של המערכת — מדריך למתכנת מצטרף

מסמך זה עונה על שאלה אחת: **איפה כל פיסת מידע חיה, ומי כותב אליה**. לרקע הכללי על הארכיטקטורה ראו [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md); כאן מתמקדים אך ורק בשכבת האחסון — טבלאות ה-DB, קבצים על הדיסק, וקובצי הקונפיג שמניעים את כל זה.

הסכימה בפועל (מקור האמת היחיד) נמצאת ב-[core/database/models.py](core/database/models.py). מסמך זה הוא ההסבר המילולי שלה — אם הם סותרים, `models.py` הוא הנכון (יכול להיות שהוא התעדכן אחרי מסמך זה).

## 1. שתי שכבות אחסון, לא אחת

המערכת **לא** שומרת קבצי PDF בתוך ה-DB. יש הפרדה נוקשה:

| שכבה | מה נשמר שם | איפה |
|---|---|---|
| **דיסק** | קבצי ה-PDF המקוריים שהורדו מאתרי החברות | `data/raw_documents/<company>/...` |
| **PostgreSQL** | כל המידע שחולץ/נגזר מהקבצים — מטא-דאטה, שדות מובנים, embeddings, התאמות | 14 טבלאות, ראו סעיף 5 |

טבלת `documents` מחזיקה רק `file_path` — מצביע לקובץ בדיסק, לא את התוכן עצמו. מי שרוצה את הטקסט/ה-PDF בפועל צריך לקרוא מהדיסק דרך `core/storage/local.py` (`LocalFileStorage`).

בנוסף יש **קבצי cache עזר** שאינם חלק מהסכימה הרשמית (לא ORM, לא נטענים אוטומטית):
- `data/*/_listing_cache.json` — תוצאת ה-listing מאתר החברה (למשל `scripts/sync_phoenix.py`), כדי לא לסרוק את האתר מחדש בכל הרצה.
- `data/processed/judge_checkpoint.jsonl` — checkpoint של `scripts/judge_matches.py --run`, כדי שהפרעה באמצע לא תחייב שיפוט חוזר של זוגות שכבר נשפטו.
- `data/processed/taxonomy_analysis/` — פלט של `scripts/analyze_coverage_taxonomy.py` (ניתוח חד-פעמי, לא נטען בחזרה על ידי שום סקריפט אחר).
- `data/json_dictionary/` — ריק כרגע, לא בשימוש.

## 2. איך מתחברים ל-DB

- מחרוזת החיבור: `DATABASE_URL` ב-`.env` (ראו `.env.example`), פורמט `postgresql+psycopg2://user:pass@host:port/dbname`.
- `core/config/settings.py: get_settings()` קורא אותה (pydantic-settings).
- `core/database/session.py`:
  - `get_engine()` — SQLAlchemy engine, יחיד לכל התהליך (`lru_cache`).
  - `session_scope()` — context manager: `commit()` בהצלחה, `rollback()` בחריגה. זו הדרך היחידה שסקריפטים בפרויקט פותחים session.
  - `init_db()` — יוצר את כל הטבלאות שלא קיימות (`Base.metadata.create_all`), ואז מריץ `_add_missing_columns()` שמוסיף עמודות חדשות לטבלאות קיימות.

**אין Alembic בפרויקט הזה, בכוונה.** במקום migrations רגילות:
1. טבלה חדשה נוצרת אוטומטית ע"י `create_all()` בפעם הראשונה שרצים כל סקריפט (כולם קוראים ל-`init_db()` בתחילתם).
2. עמודה חדשה שנוספה למודל קיים ב-`models.py` נוספת אוטומטית (כ-`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) ע"י `_add_missing_columns()` — **רק הוספה, אף פעם לא מחיקה/שינוי טיפוס**.
3. אם צריך לשנות טיפוס עמודה קיימת או למחוק אחת — זה ידני (SQL ישיר), אין כלי אוטומטי לזה. זה קרה נדיר מאוד עד כה.

## 3. מפת הזרימה: מקובץ ועד התאמה בין חברות

כל מסמך עובר שרשרת שלבים, **כל שלב הוא סקריפט נפרד תחת `scripts/`**, כל אחד קורא רק מסמכים שעדיין לא עברו את השלב שלו (idempotent — אפשר להריץ שוב ושוב בלי לעבד פעמיים). הסימון `[LLM]` = קורא ל-OpenAI, `[קוד טהור]` = דטרמיניסטי בלי קריאת מודל.

```
sync_<company>.py                     → documents                          (הורדה + metadata מהאתר)
        │
        ▼
extract_documents.py [LLM]            → document_extractions               (שדות מובנים מהטקסט הגולמי)
        │
        ▼
classify_documents.py [LLM]           → document_classifications           (איזה קטגוריה בטקסונומיה)
        │
        ▼
answer_question_bank.py [LLM]         → document_question_answers          (מאגר שאלות: בסיס + לפי קטגוריה)
                                       → document_additional_findings
        │
        ▼
build_canonical_profiles.py [LLM]     → document_canonical_profiles        (סיכום כיסוי בשפה אחידה)
        │
        ├──▶ normalize_canonical_codes.py [LLM]  → document_canonical_codes  (מיפוי לקודים מנורמלים)
        │
        └──▶ build_fingerprints.py [קוד טהור]    → document_fingerprints    (מספרים/ימים/טווחים סקלריים)

embed_documents.py [מודל מקומי]       → document_embeddings                (embedding על סמך השדות שחולצו)
        │
        ▼
judge_matches.py [LLM] (או match_documents.py — ישן יותר)
                                       → document_matches                  (התאמות בין חברות)
        │
        ▼
calibrate_matching.py [קוד טהור, ניתוח]  → match_calibration_runs         (הצעות כיול, לא נכתב אוטומטית לקונפיג)
```

כל שלב מסומן **"בוצע"** בטבלת בקרה אחת — `document_pipeline_status` — כדי שההרצה הבאה תדע מה כבר עובד ומה לא. פירוט מלא בסעיף 6.

**הערה חשובה על מצב בפועל**: `document_layered_embeddings` ו-רוב העמודות התוספתיות ב-`document_matches` (`final_score`, `score_breakdown`, `hard_constraint_failures` וכו') **קיימות בסכימה אך אינן מאוכלסות ע"י שום סקריפט קיים כרגע** — הן חלק ממנוע matching כמותי מתוכנן (`core/matching/orchestrator.py`, `scripts/match_documents_v2.py`) שטרם נכתב. `judge_matches.py`/`match_documents.py` הקיימים כותבים רק את העמודות הבסיסיות (`similarity_score`, `status`). ראו סעיף 5 לרשימה מדויקת.

## 4. עקרון הגרסאות (חשוב להבין לפני שנוגעים בקוד)

כל שלב LLM נשען על **קובץ קונפיג גרסתי** (YAML או קבוע ב-Python), לא הארד-קוד חופשי:

| קובץ קונפיג | קובע את | גרסה נוכחית |
|---|---|---|
| `core/taxonomy/data/taxonomy.v1.yaml` | עץ הקטגוריות ל-`classify_documents.py` | v1 |
| `core/knowledge_base/data/question_bank.v1.yaml` | השאלות (בסיס + לפי קטגוריה) ל-`answer_question_bank.py` | v1 |
| `core/knowledge_base/data/canonical_codes.v1.yaml` | הקודים המנורמלים ל-`normalize_canonical_codes.py` | v1 (`CODES_VERSION`) |
| `core/canonical/schema.py` (`PROFILE_VERSION`) | צורת הפרופיל הקנוני ל-`build_canonical_profiles.py` | v1 |
| `core/matching/profiles/data/*.yaml` | משקלים/ספים ל-matching (per-category + `default.v1.yaml`) | v1 |

**המנגנון**: כל טבלת תוצאה שומרת את מספר הגרסה שבה היא חושבה (`taxonomy_version`, `question_bank_version`, `profile_version` וכו', גם בשורה עצמה וגם ב-`document_pipeline_status`). סקריפט שמריץ שלב מסוים שואל "אילו מסמכים **לא** מעודכנים לגרסה הנוכחית?" — ומעבד רק אותם. **המשמעות המעשית**: אם משנים/מוסיפים גרסה חדשה של קובץ YAML (למשל `taxonomy.v2.yaml`) ומעדכנים את קבוע ה-`DEFAULT_VERSION`/`*_VERSION` בקוד — כל המסמכים הקיימים ייחשבו "לא מעודכנים" ויעובדו מחדש בהרצה הבאה. בלי שינוי גרסה, הרצה חוזרת לא עושה כלום (idempotent).

## 5. טבלת סיכום — כל 14 הטבלאות

| # | טבלה | תפקיד בקצרה | יחס למסמך | נכתבת ע"י |
|---|---|---|---|---|
| 1 | `companies` | חברת ביטוח (id + שם תצוגה) | — | כל `sync_<company>.py` (יוצר/מעדכן שורה אחת) |
| 2 | `documents` | זהות הקובץ: חברה, נתיב בדיסק, domain, מספרי נספח, חלון שיווק | — | `sync_<company>.py` / `build_<company>_db.py` |
| 3 | `document_extractions` | שדות מובנים שחולצו מהטקסט (coverage, זכאות, סכומים, תקופות, חריגים...) | 1:1 | `extract_documents.py` |
| 4 | `document_classifications` | הקטגוריה בטקסונומיה שהמסמך שויך אליה | 1:1 | `classify_documents.py` |
| 5 | `document_question_answers` | תשובה אחת לשאלה אחת ממאגר השאלות (הרבה שורות למסמך) | 1:N | `answer_question_bank.py` |
| 6 | `document_additional_findings` | ממצא חשוב שלא נכנס לשדה קיים/לשאלה קיימת | 1:N | `answer_question_bank.py` |
| 7 | `document_canonical_profiles` | "פרופיל כיסוי קנוני" — תקציר בשפה אחידה, לא תלוי-ניסוח-חברה | 1:1 | `build_canonical_profiles.py` |
| 8 | `document_canonical_codes` | חברות של המסמך בקודים מנורמלים (אירוע מכוסה / חריג / זכאות וכו') | 1:N | `normalize_canonical_codes.py` |
| 9 | `document_fingerprints` | "טביעת אצבע כמותית" — שדות סקלריים/מספריים שנגזרים בקוד טהור (ימים, גילאים, סכומים) | 1:1 | `build_fingerprints.py` (קוד טהור, בלי LLM) |
| 10 | `document_embeddings` | וקטור embedding יחיד למסמך שלם, מבוסס על השדות שחולצו | 1:1 | `embed_documents.py` |
| 11 | `document_layered_embeddings` | **קיימת בסכימה, לא מאוכלסת בפועל** — embedding לפי שכבה סמנטית (summary/coverage/exclusions וכו') | 1:N (מתוכנן) | אף סקריפט קיים לא כותב אליה |
| 12 | `document_matches` | התאמה בין שני מסמכים מחברות שונות + ציון + סטטוס (רק העמודות הבסיסיות בשימוש בפועל) | N:N | `match_documents.py` / `judge_matches.py` |
| 13 | `match_calibration_runs` | לוג של ניתוח כיול (הצעות משקלים/ספים) — לא מקור אמת, אדם צריך לאשר ידנית | — | `calibrate_matching.py` |
| 14 | `document_pipeline_status` | לוח בקרה: לכל מסמך, איזה שלב רץ עליו ובאיזו גרסה | 1:1 | כל סקריפט LLM-driven (2–9 למעלה) |

## 6. פירוט לכל טבלה

### שכבה 0 — זהות בסיסית

**`companies`** — שורה אחת לכל פלאגין חברה: `id` (למשל `"migdal"`, `"phoenix"`), `display_name`. תואם תמיד לתיקייה תחת `companies/<name>/`.

**`documents`** — ליבת הזהות. `id` (UUID), `company_id` (FK), `original_file_name`, `file_path` (יחסי לדיסק, לא ה-bytes עצמם), `domain` (`health`/`life`/`mixed`), `appendix_number` (`ARRAY(String)` — מסמך יכול להכיל כמה נספחים), `appendix_name`, `department_name`, `pages_count`, `extraction_method` (`text`/`ocr`/`manual`), `marketing_start_date`/`marketing_end_date` (חלון שיווק — כרגע רק להראל יש ערך אמיתי; `NULL` בכל שאר החברות נקרא כ"אין סימן, פעיל כרגע", ראו `Document.is_active`), `created_date`.

### שכבה 1 — חילוץ שדות גולמי (LLM)

**`document_extractions`** — עמודות ייעודיות להשוואה: `coverage_type`, `coverage_name`, `eligibility_conditions`, `insurance_amounts` (ARRAY), `qualifying_period`/`waiting_period` (טקסט חופשי בשלב הזה, עוד לא ימים), `exclusions` (ARRAY), `age_range`, `restrictions` (ARRAY), `tables` (JSONB), `disease_count`/`disease_list`/`survival_period`. + `raw_extraction` (JSONB) — הפלט המלא של המודל, רשת ביטחון לשחזור בלי לקרוא ל-LLM שוב (שימש בפועל לתיקון באג ב-`sync_directinsurance.py`, ראו PROJECT_OVERVIEW.md).

### שכבה 2 — סיווג טקסונומי (LLM)

**`document_classifications`** — לאיזו קטגוריה בעץ הטקסונומיה (`core/taxonomy/data/taxonomy.v1.yaml`) המסמך שייך: `category_id`, `main_category`, `coverage_family`, `coverage_subtype`, `coverage_variant`, `benefit_model`, `target_population`, `alternative_categories` (JSONB — קטגוריות נוספות סבירות, כדי שסיווג לא חד-משמעי לא יאבד מועמד אמיתי ב-matching), `confidence`, `evidence`, `raw_response` (JSONB).

### שכבה 3 — מאגר שאלות (LLM)

**`document_question_answers`** — הרבה שורות למסמך (שאלות בסיס + שאלות ספציפיות לקטגוריה). כל שורה: `question_id`, `question_scope` (`base`/`category`), `status` (**ארבעה** ערכים מכוונים, לא "יש/אין" בינארי: `FOUND` / `NOT_FOUND` = המסמך שותק על זה / `NOT_APPLICABLE` = לא רלוונטי לסוג הכיסוי הזה / `AMBIGUOUS` = המודל לא היה בטוח), `answer_text`, `evidence_text`/`evidence_page`/`evidence_section` (למה זה מבוסס), `confidence`. `unique index` על `(document_id, question_bank_version, question_id)`.

**`document_additional_findings`** — ממצאים חשובים שלא מתאימים לשום שדה/שאלה קיימים: `finding_text`, `related_field`, `evidence_page`.

### שכבה 4 — פרופיל קנוני + קודים מנורמלים (LLM)

**`document_canonical_profiles`** — "פרופיל כיסוי קנוני": תקציר שכולו בשפה אחידה, לא תלוי בניסוח הספציפי של כל חברה. שדות עיקריים: `insured_event`, `covered_events`/`covered_conditions` (JSONB), `exclusions_normalized`/`limitations` (JSONB), `eligibility_normalized`, `waiting_period_days`/`qualifying_period_days`/`survival_period_days` (**שימו לב**: `NULL` בשלב הזה בכוונה — ההמרה מטקסט למספר ימים קורה רק בשלב הבא, `build_fingerprints.py`), `benefit_type`/`benefit_calculation`, `amounts`/`caps` (JSONB), `deductible`/`age_restrictions` (JSONB), `definitions` (JSONB — רשימת `{term, definition}`, לא dict חופשי, כי OpenAI structured output דורש מפתחות קבועים), `raw_profile` (JSONB, אותו עיקרון audit-copy כמו `raw_extraction`).

**`document_canonical_codes`** — הרבה שורות למסמך: חברות בקודים מנורמלים מתוך `core/knowledge_base/data/canonical_codes.v1.yaml` (למשל אירוע מכוסה ספציפי, חריג ספציפי). `code_category`, `code`, `raw_phrase` (הביטוי המקורי שממנו נגזר הקוד), `source_field`, `confidence`. **זו קבוצת הנתונים שעליה מחושב Jaccard/weighted-Jaccard similarity** ב-`core/matching/quantitative_score.py` (המנוע הכמותי המתוכנן).

### שכבה 5 — טביעת אצבע כמותית (קוד טהור, בלי LLM)

**`document_fingerprints`** — נגזר לגמרי מ-`document_canonical_profiles` + `document_canonical_codes` + `document_question_answers` בקוד (`core/fingerprint/builder.py` + `parsers.py`), **בלי קריאת LLM נוספת** — דטרמיניסטי, אפשר לבנות מחדש בכל רגע. כולל: `waiting_period_days`/`qualifying_period_days`/`survival_period_days` (המספרים בפועל, ממירים את הטקסט מ-`document_canonical_profiles`), `min_entry_age`/`max_entry_age`/`termination_age`, `benefit_amount_min`/`max`/`currency`, `benefit_percentage`, `maximum_benefit`, `deductible_amount`, `covered_event_count`/`major_exclusion_count`/`special_condition_count`, `raw_features` (JSONB).

### שכבה 6 — embeddings

**`document_embeddings`** — embedding יחיד למסמך שלם: `document_id` (PK/FK), `embedding` (`ARRAY(Float)` — **לא pgvector**, לא מותקן על ה-Postgres המקומי; בסדר גודל הנוכחי, cosine similarity ב-numpy בזיכרון מהיר מספיק), `model_name` (כרגע `intfloat/multilingual-e5-large`, מודל מקומי). מחושב על **השדות שחולצו**, לא על הטקסט הגולמי — כדי שניסוח שונה בין חברות לא ישבש את ההתאמה.

**`document_layered_embeddings`** — **קיימת בסכימה בלבד**, אף סקריפט לא כותב אליה כרגע. הרעיון (לפי `models.py`): embedding נפרד לכל שכבה סמנטית (`summary`/`coverage`/`insured_event`/`definitions`/`exclusions`/`eligibility`/`benefit_structure`/`canonical_profile`) לשליפת מועמדים, לא כהחלטת ההתאמה הסופית. PK מורכב `(document_id, layer)`.

### שכבה 7 — Matching

**`document_matches`** — התאמה מועמדת בין שני מסמכים מחברות שונות. `id` = `f"{document_id}:{matched_document_id}"` (דטרמיניסטי, לא UUID רנדומלי — מאפשר `upsert`/`merge` נקי). **עמודות בשימוש בפועל היום**: `document_id`, `matched_document_id`, `similarity_score`, `status` (`auto_confirmed`/`pending_review`/`confirmed`/`rejected` — שתי הראשונות נכתבות אוטומטית, שתי האחרונות רק ע"י אדם דרך ה-Dashboard). **עמודות תוספתיות קיימות בסכימה, `NULL` תמיד כרגע** (מנוע matching כמותי מתוכנן, לא נבנה עדיין): `final_score`, `score_breakdown` (JSONB), `critical_mismatches`/`material_differences`/`missing_features` (JSONB), `best_candidate_score`/`second_candidate_score`/`candidate_margin`, `mutual_match`, `group_validation_status`, `match_stage`, `hard_constraint_failures` (JSONB), `matching_profile_version`, `auditor_verdict`/`auditor_reasoning` (מנוע ה-LLM judge, `core/matching/semantic_judge.py`, כותב את התוצאה הסופית שלו רק לתוך `status`/`similarity_score` היום — לא לעמודות ה-auditor הייעודיות).

חשוב: **`match_documents.py`/`judge_matches.py` מוחקים ובונים מחדש את כל הטבלה בכל הרצה, חוץ משורות שכבר עברו סקירה אנושית** (`status in {confirmed, rejected}`) — אלו "מוגנות" (`protected_ids`) ולעולם לא נמחקות/נדרסות אוטומטית.

**`match_calibration_runs`** — לוג audit של `scripts/calibrate_matching.py`: ניתוח סטטיסטי על ה-`document_matches` שכבר נסקרו ע"י אדם, **לא מקור אמת בעצמו** — רק הצעה. `category_id`, `sample_size`, `feature_importance` (JSONB), `hard_constraints_proposed`/`weights_proposed`/`thresholds_proposed` (JSONB), `notes`, `profile_version_written`. אדם קורא את התוצאה ומעדכן ידנית קובץ YAML תחת `core/matching/profiles/data/{category}.v*.yaml`.

### לוח הבקרה — `document_pipeline_status`

זו הטבלה שהופכת את כל השרשרת (סעיף 3) ל**נכפית מכנית**, לא רק מוסכמה בין מפתחים: `document_id` (PK), ולכל שלב זוג עמודות `<שלב>_at` (מתי בוצע, `NULL` = טרם) + `<שלב>_version` (באיזו גרסת קונפיג בוצע):

```
classified_at            / taxonomy_version
questions_answered_at    / question_bank_version
canonical_profile_at     / profile_version
canonical_codes_at       / canonical_codes_version
fingerprint_at           / fingerprint_version
layered_embeddings_at    / embedding_model_name
```

כל סקריפט LLM-driven שואל בתחילת ריצה: "אילו מסמכים כבר יש להם `document_extractions`/שלב-קודם, אבל `<השלב-שלי>_version` **שונה** מהגרסה הנוכחית (או `NULL`)?" — ורק אלה נכנסים לעיבוד. זה מה שהופך "מסמך חדש נוגע רק בקטגוריה שלו, לא בכל הקורפוס" לעובדה מכנית ולא רק כוונה טובה.

## 7. קבצי הקונפיג ש"מזינים" את הטבלאות (לא ב-DB בכלל)

הידע התחומי (מה השאלות, מה הקטגוריות, מה הקודים המנורמלים) חי כקבצים גרסתיים, לא בטבלאות:

| קובץ | מזין את |
|---|---|
| `core/taxonomy/data/taxonomy.v1.yaml` | `document_classifications` |
| `core/knowledge_base/data/question_bank.v1.yaml` | `document_question_answers` |
| `core/knowledge_base/data/canonical_codes.v1.yaml` | `document_canonical_codes` |
| `core/knowledge_base/data/concepts.v1.yaml` | הגדרות מושגים תומכות (לא טבלה ייעודית) |
| `core/matching/profiles/data/default.v1.yaml` + `health.critical_illness.{cancer,general}.v1.yaml` | ה-matching (משקלים/ספים/hard constraints לכל קטגוריה) |

כדי לשנות "מה נחשב אותו כיסוי" או "אילו שאלות שואלים" — עורכים את ה-YAML, מעלים גרסה, ומריצים מחדש את הסקריפט הרלוונטי (ראו סעיף 4).

## 8. תוכן טבלת `companies` כיום

9 חברות רשומות, כל אחת עם פלאגין תחת `companies/<id>/`: `migdal`, `phoenix`, `clal`, `menorah`, `directinsurance`, `harel`, `aig`, `ayalon`, `hachshara`. פירוט מקורות/הגנות/כמות מסמכים לכל חברה — ב-PROJECT_OVERVIEW.md (מתועד שם עד 5 החברות הראשונות; ה-4 הנוספות נבנו באותו pattern, `companies/<name>/{config,downloader,parser,extractor,rules}.py`).

## 9. איך לחקור את ה-DB בפועל

**דרך Python (מומלץ בתוך סקריפט/ניסוי):**
```python
from sqlalchemy import select
from core.database.session import session_scope
from core.database.models import Document, DocumentExtraction

with session_scope() as session:
    rows = session.scalars(
        select(Document).where(Document.company_id == "migdal").limit(5)
    ).all()
```

**דרך `psql` ישירות** (מחרוזת החיבור מ-`.env`):
```bash
psql "postgresql://app_user:app_password@localhost:5432/insurance_ai_platform"
\dt                                   -- רשימת כל הטבלאות
SELECT company_id, count(*) FROM documents GROUP BY company_id;
SELECT * FROM document_pipeline_status WHERE fingerprint_at IS NULL LIMIT 10;
```

## 10. מסמכים קשורים

- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — ארכיטקטורה מלאה, היסטוריית החלטות לכל חברה, why לא רק what.
- [OVERVIEW_TECH_SIMPLE.md](OVERVIEW_TECH_SIMPLE.md) — תמצית טכנית קצרה (מתייחסת לגרסה מוקדמת יותר של הסכימה, 5 טבלאות בלבד — מסמך זה הוא המעודכן מבין השניים).
- [OVERVIEW_SIMPLE.md](OVERVIEW_SIMPLE.md) — הסבר לא-טכני, שלב אחר שלב.
- [core/database/models.py](core/database/models.py) — מקור האמת הפורמלי לכל מה שמתואר כאן.
