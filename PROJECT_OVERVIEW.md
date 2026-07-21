# Enterprise Processing Platform — סקירת פרויקט

מסמך זה מתעד את הארכיטקטורה, ההגדרות וההחלטות שהתקבלו עד כה, כדי שנוכל להמשיך ביעילות לשלב הפרונט ואז לחברות ביטוח נוספות בלי לאבד הקשר.

## עקרון-על

כל חברת ביטוח היא **פלאגין עצמאי** תחת `companies/<name>/` ללא שום תלות בחברות אחרות. כל הקוד המשותף (DB, PDF, OCR, extraction, storage) חי תחת `core/` ואינו יודע דבר על חברה ספציפית. הוספת חברה חדשה = יצירת תיקייה חדשה בלבד; אין צורך לגעת ב-`core/` או בחברות קיימות.

## מבנה הפרויקט

```
insurance_ai_platform/
├── main.py                     # FastAPI app: GET /health + api_router (dashboard)
├── pyproject.toml              # ruff + mypy + pytest config
├── requirements.txt / requirements-dev.txt
├── .env / .env.example         # DATABASE_URL, DATA_DIR, LOG_*, OCR, OPENAI_API_KEY, ...
├── core/
│   ├── config/settings.py      # Settings (pydantic-settings) + get_settings()
│   ├── database/                # models.py (SQLAlchemy 2.0), session.py
│   ├── exceptions.py            # PlatformError + תת-מחלקות
│   ├── extraction/              # appendix_number.py, text_extraction.py, schema.py, llm_extract.py
│   ├── embeddings/               # model.py — local multilingual-e5 wrapper
│   ├── matching/                 # similarity.py — cross-company cosine matching
│   ├── models/                  # document.py (DocumentIdentity), enums.py
│   ├── ocr/                     # engine.py (Tesseract), preprocessing.py, regions.py
│   ├── pdf_processing/          # document.py — PdfDocument (PyMuPDF wrapper)
│   ├── plugins/                 # base.py (ABCs), registry.py (CompanyRegistry)
│   ├── storage/                 # base.py (ABC), local.py (LocalFileStorage)
│   ├── utils/                   # hashing.py, logging.py
│   ├── indexing/ llm/ rag/      # placeholder ריק — לא בשימוש, שולבו בפועל תחת extraction/embeddings/matching
├── api/                          # FastAPI routes for the dashboard — קריאה-בלבד, ללא LLM
├── frontend/                     # React + Vite + TypeScript, dashboard (RTL, ללא UI framework)
├── companies/
│   ├── migdal/   {__init__,config,downloader,parser,extractor,rules}.py
│   ├── phoenix/  {__init__,config,downloader,parser,extractor,rules}.py
│   └── clal/     {__init__,config,downloader,parser,extractor,rules}.py
├── data/
│   ├── raw_documents/{migdal,phoenix,clal}/...   # קבצי PDF אמיתיים
│   ├── processed/ json_dictionary/          # ריק — שלבים עתידיים
├── scripts/     # CLI entry points, ראה טבלה למטה
├── tessdata/    # heb.traineddata, eng.traineddata (gitignored)
└── tests/       # כולל tests/core/, tests/test_api_routes.py, tests/test_main.py
```

## חוזה הפלאגין (`core/plugins/base.py`, `registry.py`)

כל חברה חייבת לממש 4 ABCs + קונפיג:

- **`CompanyConfig(BaseModel)`** — `company_id`, `display_name`, `enabled=True`. כל חברה יורשת ומוסיפה שדות ספציפיים.
- **`BaseDownloader`** — `download_all(destination_dir, limit=None) -> list[Path]`.
- **`BaseParser`** — `extract_text(file_path) -> str` (טקסט מוטמע בלבד, לא OCR; `""` אם אין).
- **`BaseExtractor`** — `extract_fields(file_path, text) -> dict[str, list[str] | str | None]`. מקבל גם path וגם טקסט; אחראי בעצמו על fallback ל-OCR דרך `BaseRules` אם `text` ריק.
- **`BaseRules`** — `get_ocr_crop_regions(page_number) -> list[tuple[float,float,float,float]]` (קופסאות normalized 0.0–1.0).
- **`CompanyPlugin`** — dataclass שמאגד את כל הארבעה + config.

**`CompanyRegistry`**: `register()` / `get()` / `list_companies()`. `discover_plugins(registry)` עובר על כל תת-מודול של `companies/` וקורא לפונקציית `register(registry)` שכל חברה חושפת ב-`__init__.py` שלה.

### להוספת חברה חדשה
1. `companies/<name>/config.py` — יורש מ-`CompanyConfig`.
2. `downloader.py`, `parser.py`, `extractor.py`, `rules.py` — יורשים מה-ABCs.
3. `__init__.py` עם `def register(registry): ...` (ראה `companies/migdal/__init__.py` או `companies/phoenix/__init__.py` כדוגמה).
4. שום שינוי ב-`core/` נדרש.

## סכמת ה-DB (PostgreSQL, `core/database/models.py`)

Policies/Appendices/OCR_Results/Extracted_Text/Processing_Logs נדחו עד שיהיה בהם צורך אמיתי. אין Alembic — הסכמה מנוהלת רק דרך `Base.metadata.create_all()` ב-`init_db()` (טבלאות חדשות נוצרות אוטומטית, לא נוגע בקיימות).

**`companies`**: `id` (PK), `display_name`.

**`documents`**: `id` (PK, uuid), `company_id` (FK), `original_file_name`, `file_path`, `domain` (health/life/mixed), `appendix_number` (`ARRAY(String)` — יכול להכיל כמה מספרים), `appendix_name`, `department_name`, `pages_count`, `extraction_method`, `created_date`.

**`document_extractions`**: שדות מובנים שחולצו ע"י LLM לכל מסמך (1:1 עם `documents` דרך `document_id`, unique). עמודות ייעודיות להשוואה: `coverage_type`, `coverage_name`, `eligibility_conditions`, `insurance_amounts` (ARRAY), `qualifying_period` (תקופת אכשרה), `waiting_period` (תקופת המתנה), `exclusions` (ARRAY), `age_range`, `restrictions` (ARRAY), `tables` (JSONB), `disease_count`, `disease_list` (ARRAY), `survival_period`. + `raw_extraction` (JSONB, הפלט המלא כרשת ביטחון).

**`document_embeddings`**: `document_id` (PK/FK), `embedding` (`ARRAY(Float)` — **לא pgvector**, לא מותקן על ה-Postgres המקומי; בסדר גודל של אלפי מסמכים, cosine similarity ב-numpy בזיכרון מהיר מספיק), `model_name`.

**`document_matches`**: `id` (`f"{doc_id}:{matched_doc_id}"`, דטרמיניסטי), `document_id`, `matched_document_id`, `similarity_score`, `status` (`MatchStatus`: auto_confirmed/pending_review/confirmed/rejected).

## חברות שנבנו

### מגדל (`companies/migdal/`)
- מקור: `my.migdal.co.il` — API לא-מוגן (unlike `front.migdal.co.il` שמאחורי Incapsula WAF), נגיש עם `httpx` רגיל + headers מזויפים, בלי דפדפן.
- סיווג health/life/mixed לפי טקסונומיית `Department` (`classify_department()`).
- מספר הנספח **לא** מגיע ממטא-דאטה אמינה — נקרא מתוך תוכן העמוד: קודם טקסט מוטמע (page 1), fallback ל-OCR ממוקד (רק 15% התחתונים של עמוד 1, `TesseractEngine`), ורק כמוצא אחרון — רמז משם הקובץ.
- **1053 מסמכים ב-DB**, 1055 קבצים בדיסק (785 health / 230 life / 40 mixed).

### הפניקס (`companies/phoenix/`)
- מקור: `fnx.co.il/spf/Iframe_FormsConditions.aspx` — טופס ASP.NET מאחורי CloudFront+AWS WAF Bot Control שחוסם Chromium headless רגיל (403). עוקפים עם Playwright + הסתרת `navigator.webdriver` + UA/locale אמיתיים. הורדות PDF עצמן הן `httpx` רגיל (לא מוגן).
- מספר נספח, שם וכו' מגיעים **ישירות מטבלת התוצאות באתר** — אין קריאת קובץ/OCR בכלל (`PhoenixExtractor`/`PhoenixRules` הם no-op בכוונה).
- **אתגר מרכזי שנפתר**: שאילתת בריאות לא-מסוננת (`cover=""`) נתקעת בעמוד 10 באופן בלתי אמין (bug אמיתי באתר, אושר חי). הפתרון: איטרציה על 15 תתי-קטגוריות (`DOMAIN_COVERS["health"]`) עם דה-דופליקציה לפי `download_url`; השאילתה הלא-מסוננת הוסרה לגמרי אחרי שאושר שהיא subset מלא של תתי-הקטגוריות.
- Resilience: retry עם backoff אקספוננציאלי על עמודים "מוקדמים מדי" (לפי רמז ה-"עמוד אחרון" מהאתר), reset session (context חדש) אחרי מיצוי retries, ו-retry נפרד על שגיאות 5xx חולפות בהורדת קבצים (404 לא מנוסה מחדש — זה קישור שבור אמיתי).
- `scripts/sync_phoenix.py` שולף את הרשימה **פעם אחת** (השלב האיטי — כשעה) ומשתמש בה גם להורדה וגם לאכלוס ה-DB, עם cache ל-`_listing_cache.json` כדי לא לסרוק מחדש בכל הרצה.
- **1903 מסמכים ב-DB** (871 health / 1032 life), 0 עם appendix_number ריק.

### כלל (`companies/clal/`)
- מקור: `clalbit.co.il/policysearch/` — Angular SPA שמאחורי Umbraco CMS, קורא ל-`/umbraco/api/SearchApi/SearchPolicies`. ה-API עצמו מאחורי bot-management (עוגיות בסגנון Imperva/Akamai, prefix `TS...`) — קריאה ישירה ללא session דפדפן אמיתי מחזירה 404 "No HTTP resource" גם עם פרמטרים תקינים לגמרי (אושר חי). דורש טעינת עמוד החיפוש + בחירת הדרופדאונים + לחיצה על כפתור החיפוש דרך Playwright כדי לקבל cookies תקפים; הורדות PDF עצמן (תחת `/media/`) הן `httpx` רגיל, בלי הגנה (אושר חי).
- מספר נספח, שם וכו' מגיעים **ישירות מה-JSON של ה-API** (`AttachmentNumber`) — אין קריאת קובץ/OCR בכלל (`ClalExtractor`/`ClalRules` הם no-op בכוונה, כמו הפניקס).
- **פשוט משמעותית מהפניקס**: ה-API מחזיר את **כל** התוצאות בקריאה אחת (`TotalResultCount` תואם בדיוק למספר השורות שחזרו, גם עבור 290 בריאות וגם 215 חיים) — אין pagination בכלל, אין את הבעיה של "עמוד 10 תקוע" שהייתה בהפניקס.
- "מחלות קשות" אינה Family נפרדת באתר כלל — היא תת-קטגוריה בתוך "בריאות" (`Family=1520`), בדיוק כמו שכבר קורה במגדל ובהפניקס.
- **שתי ישויות "כלל" נפרדות עם דוקומנטים שלא חופפים בכלל**: ה-Company dropdown באתר מכיל גם "כלל ביטוח" (id=1) וגם "כלל בריאות" (id=9) — אושר חי ש-id=9 מחזיר 81 מסמכי בריאות נוספים עם **אפס** חפיפה מול id=1 (כמו "אחריות לחיים סרטן" — כיסויי מחלות קשות/סרטן). `ClalConfig.company_filter_ids` שולף לכל domain את שני ה-IDs (id=9 החזיר 0 נוספים ל-חיים, אבל נבדק בכל זאת). `ClalDownloader.list_documents()` מאחד ומדדפל לפי `download_url` בין הצירופים.
- `scripts/sync_clal.py` (מבנה זהה ל-`sync_phoenix.py`): שולף רשימה פעם אחת (מהירה — כמה שניות, לא כשעה כמו הפניקס), עם cache ל-`_listing_cache.json`.
- **527 מסמכים ב-DB** (364 health / 163 life), 31 עם appendix_number ריק (94.1% כיסוי אחרי backfill).

## הערה חשובה: תנודתיות באתר הפניקס

מספר המסמכים שנמצא בסריקות שונות של הפניקס השתנה מעט בין ריצות (972↔944 בריאות, 1015↔1135↔1075 חיים) — כנראה תוצאה של אי-יציבות בצד השרת שלהם (rate limiting / load balancer) ולא bug בקוד שלנו. ה-DB מצטבר בין ריצות (merge לפי path, לא מוחק), כך שהמצב הסופי (1903) הוא איחוד של כל מה שנמצא אי-פעם, לא רק הריצה האחרונה.

## סקריפטים (`scripts/`)

| קובץ | מטרה |
|---|---|
| `download_migdal.py` | הורדת ארכיון מגדל (`--limit N` אופציונלי) |
| `build_migdal_db.py` | חילוץ ואכלוס DB עבור מגדל |
| `download_phoenix.py` | הורדת ארכיון הפניקס בלבד |
| `build_phoenix_db.py` | אכלוס DB עבור הפניקס (ללא sync — סורק listing מחדש) |
| `sync_phoenix.py` | **המומלץ** — listing פעם אחת, הורדה + DB יחד, עם cache |
| `setup_tessdata.py` | הורדת heb/eng traineddata ל-Tesseract |
| `extract_documents.py` | חילוץ שדות מובנים דרך OpenAI Batch API (`--limit N`; idempotent — מדלג על מסמכים שכבר חולצו) |
| `embed_documents.py` | embedding מקומי (multilingual-e5) לכל מסמך עם extraction אבל בלי embedding |
| `match_documents.py` | חישוב התאמות חוצות-חברות מתוך ה-embeddings הקיימים |

**אזהרה חשובה**: `extract_documents.py` **בלי** `--limit` רץ על **כל** המסמכים הממתינים (יכול להיות אלפים) וקורא את הטקסט של כולם (כולל OCR) לפני ששולח batch אחד בסוף — תהליך שיכול לקחת שעות, ואם הוא נהרג/קורס באמצע, **כל ההתקדמות אובדת** (אין checkpointing חלקי). תמיד להשתמש ב-`--limit` לבדיקות; אם רצים ריצה מלאה, לתת לזה לרוץ עד הסוף בלי להפריע.

## חילוץ שדות + embeddings + matching (`core/extraction/`, `core/embeddings/`, `core/matching/`)

מטרה: לחלץ מכל מסמך שדות מובנים אחידים (לא 20 שאלות חופשיות), ואז למפות אילו נספחים אצל חברות שונות מתארים את אותו כיסוי בפועל — **גם כשמספרי הנספח שונים לגמרי** (הם ספציפיים לכל חברה). ברגע שהמיפוי קיים, השוואה בין חברות היא שליפת DB טהורה, בלי שום קריאת LLM.

- **LLM**: **OpenAI** (`gpt-4.1-mini` כברירת מחדל), **לא Anthropic** — חשבון ה-Anthropic של המשתמש נתקל בחסימת "Identity verification is required to continue" ברמת הארגון, שלא נפתרה לא דרך API key ולא דרך OAuth (`ant auth login`) גם אחרי כמה ניסיונות אמיתיים. `core/extraction/llm_extract.py` בנוי סביב **Message Batches API** (זול פי 2, מתאים לעיבוד חד-פעמי/תקופתי בכמויות) + **Structured Outputs** (JSON Schema strict mode) להבטחת JSON תקין תמיד.
- **Embeddings**: מודל מקומי חינמי (`intfloat/multilingual-e5-large` דרך `sentence-transformers`), לא API בתשלום. ה-embedding מבוסס על **השדות שחולצו**, לא על הטקסט הגולמי — כדי שניסוח שונה בין חברות לא ישבש את ההתאמה.
- **Matching**: cosine similarity (numpy) מוגבל לאותו domain (health/life) ולחברה **שונה** בלבד; ≥95% (`similarity_auto_confirm_threshold`) → `auto_confirmed`, מתחת → `pending_review` לבדיקה ידנית ב-Dashboard.

## Dashboard (`api/`, `frontend/`)

קריאה-בלבד כרגע, בלי שום LLM call: מציג קבצים שהורדו, חילוצים (עם פאנל פרטים ללחיצה על מסמך), והתאמות (מאושרות אוטומטית / ממתינות לבדיקה) עם אחוז דמיון. כפתור "השוואה" קיים במסך הראשי אבל לא פותח כלום עדיין (`disabled`) — זה השלב הבא.

- Backend: `api/routes.py` (FastAPI `APIRouter`, `GET /api/documents|extractions/{id}|matches`), CORS ל-`localhost:5173` ב-`main.py`.
- Frontend: React + Vite + TypeScript תחת `frontend/`, בלי ספריית UI (במכוון, "פרונט פשוט מאוד"), RTL.
- **מלכודת אמיתית שנתפסה בבדיקה חיה**: מזהי מסמך מכילים `/` ממשי (למשל `"phoenix:phoenix/health/x.pdf"`) — נתיב FastAPI רגיל (`{document_id}`) לא תואם את זה גם כשה-frontend מקודד עם `encodeURIComponent`. הפתרון: `{document_id:path}` בצד השרת, וקידוד per-segment (לא של כל המחרוזת) בצד הלקוח. יש טסט רגרסיה לזה.
- הרצה מקומית: `uvicorn main:app --port 8000` + (בתיקיית `frontend/`) `npm run dev` (פורט 5173).

## החלטות עיצוב מרכזיות

1. **זהות מסמך היא הדבר החשוב ביותר** — כל מסמך תמיד מקושר ל: חברה, שם קובץ מקורי, מספר נספח (רשימה, לא ערך יחיד — מסמך יכול להכיל כמה נספחים), domain, שיטת חילוץ.
2. **דה-דופליקציה לפי content hash** — לא לפי URL/שם קובץ, כדי למנוע קבצים כפולים גם כשהאתר עצמו מציג את אותו תוכן תחת שמות/נספחים שונים.
3. **אמון בגוף המקור, לא בניחוש** — כשחברה נותנת מספר נספח מובנה במטא-דאטה (הפניקס) משתמשים בו ישירות; כשלא (מגדל), קוראים בפועל את תוכן הקובץ ולא מסתמכים על השם.
4. **התנהגות מנומסת כלפי האתרים** — delays, backoff, retry מוגבל (לא אינסופי), ועצירה לחשוב כשנראה שהאתר חוסם/מואט אותנו, במקום להסלים אוטומטית.

## מה הבא

1. **חברות נוספות** — מודולרי, לפי אותו pattern (`companies/<name>/`); מגדל+הפניקס+כלל כבר בפנים, כל מסמך חדש עובר באותו pipeline חילוץ+embedding+matching בלי שום שינוי קוד.
2. **בדיקת איכות ה-matching** — לעבור בדשבורד על מדגם מהתאמות (במיוחד כלל מול מגדל/הפניקס, טרי) ולוודא שהן הגיוניות, כולל שימוש בציון "מי עדיף" (כלל אצבע חינמי, ר' `frontend/src/scoring.ts`) לבדיקת סבירות.
3. **checkpointing** — `scripts/extract_documents.py`/`embed_documents.py` רצים כעת ב-chunks (`--chunk-size`, ברירת מחדל 100/200) עם שמירה ל-DB אחרי כל chunk — קריסה/הפרעה מאבדת לכל היותר chunk אחד, לא ריצה שלמה. הרצה חוזרת של אותה פקודה ממשיכה אוטומטית מאיפה שנעצר (מסמכים שכבר חולצו/הוטמעו מדולגים).
4. **טיימאאוט OCR** — `core/ocr/engine.py` עם `ocr_timeout_seconds` (ברירת מחדל 120) כדי שעמוד סרוק פגום לא יתקע את כל הריצה לצמיתות.
3. **חברות נוספות** — מודולרי, לפי אותו pattern (`companies/<name>/`), ללא שינוי ב-`core/`; כל מסמך חדש עובר באותו pipeline חילוץ+embedding+matching בלי שום שינוי קוד.
4. **עתידי (לא נבנה עוד)**: OCR_Results / Extracted_Text / Processing_Logs / Appendices / Policies tables, JSON Knowledge Dictionary — נדחה עד שיהיה בהם שימוש קונקרטי.
