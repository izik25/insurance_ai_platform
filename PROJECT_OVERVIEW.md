# Enterprise Processing Platform — סקירת פרויקט

מסמך זה מתעד את הארכיטקטורה, ההגדרות וההחלטות שהתקבלו עד כה, כדי שנוכל להמשיך ביעילות לשלב הפרונט ואז לחברות ביטוח נוספות בלי לאבד הקשר.

## עקרון-על

כל חברת ביטוח היא **פלאגין עצמאי** תחת `companies/<name>/` ללא שום תלות בחברות אחרות. כל הקוד המשותף (DB, PDF, OCR, extraction, storage) חי תחת `core/` ואינו יודע דבר על חברה ספציפית. הוספת חברה חדשה = יצירת תיקייה חדשה בלבד; אין צורך לגעת ב-`core/` או בחברות קיימות.

## מבנה הפרויקט

```
insurance_ai_platform/
├── main.py                     # FastAPI app (GET /health בלבד כרגע)
├── pyproject.toml              # ruff + mypy + pytest config
├── requirements.txt / requirements-dev.txt
├── .env / .env.example         # DATABASE_URL, DATA_DIR, LOG_*, OCR settings
├── core/
│   ├── config/settings.py      # Settings (pydantic-settings) + get_settings()
│   ├── database/                # models.py (SQLAlchemy 2.0), session.py
│   ├── exceptions.py            # PlatformError + תת-מחלקות
│   ├── extraction/              # appendix_number.py — regex לזיהוי "נספח N"
│   ├── models/                  # document.py (DocumentIdentity), enums.py
│   ├── ocr/                     # engine.py (Tesseract), preprocessing.py, regions.py
│   ├── pdf_processing/          # document.py — PdfDocument (PyMuPDF wrapper)
│   ├── plugins/                 # base.py (ABCs), registry.py (CompanyRegistry)
│   ├── storage/                 # base.py (ABC), local.py (LocalFileStorage)
│   ├── utils/                   # hashing.py, logging.py
│   ├── indexing/ llm/ rag/      # placeholder ריק — שלב 6 עתידי, לא נבנה עדיין
├── companies/
│   ├── migdal/   {__init__,config,downloader,parser,extractor,rules}.py
│   └── phoenix/  {__init__,config,downloader,parser,extractor,rules}.py
├── data/
│   ├── raw_documents/{migdal,phoenix}/...   # קבצי PDF אמיתיים
│   ├── processed/ json_dictionary/          # ריק — שלבים עתידיים
├── scripts/     # CLI entry points, ראה טבלה למטה
├── tessdata/    # heb.traineddata, eng.traineddata (gitignored)
└── tests/       # 22 קבצי טסט, 91 טסטים
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

רק שתי טבלאות נבנו עד כה (Policies/Appendices/OCR_Results/Extracted_Text/Processing_Logs נדחו עד שיהיה בהם צורך אמיתי):

**`companies`**: `id` (PK), `display_name`.

**`documents`**: `id` (PK, uuid), `company_id` (FK), `original_file_name`, `file_path`, `domain` (health/life/mixed), `appendix_number` (`ARRAY(String)` — יכול להכיל כמה מספרים), `appendix_name`, `department_name`, `pages_count`, `extraction_method`, `created_date`.

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

## Tooling

- `requirements.txt`: fastapi, uvicorn, pydantic(-settings), httpx, pymupdf, opencv-contrib-python, pytesseract, sqlalchemy, psycopg2-binary, playwright.
- `requirements-dev.txt`: pytest, ruff, mypy.
- ruff: line-length 100, py312, `select = ["E","F","I","UP","B"]`.
- mypy: `disallow_untyped_defs=true`, strict-ish.
- OCR: **Tesseract** (לא PaddleOCR — אין לו מודל עברית).

## החלטות עיצוב מרכזיות

1. **זהות מסמך היא הדבר החשוב ביותר** — כל מסמך תמיד מקושר ל: חברה, שם קובץ מקורי, מספר נספח (רשימה, לא ערך יחיד — מסמך יכול להכיל כמה נספחים), domain, שיטת חילוץ.
2. **דה-דופליקציה לפי content hash** — לא לפי URL/שם קובץ, כדי למנוע קבצים כפולים גם כשהאתר עצמו מציג את אותו תוכן תחת שמות/נספחים שונים.
3. **אמון בגוף המקור, לא בניחוש** — כשחברה נותנת מספר נספח מובנה במטא-דאטה (הפניקס) משתמשים בו ישירות; כשלא (מגדל), קוראים בפועל את תוכן הקובץ ולא מסתמכים על השם.
4. **התנהגות מנומסת כלפי האתרים** — delays, backoff, retry מוגבל (לא אינסופי), ועצירה לחשוב כשנראה שהאתר חוסם/מואט אותנו, במקום להסלים אוטומטית.

## מה הבא

1. **שלב פרונט** — ממשק לצפייה/חיפוש במסמכים שכבר יש ב-DB (מגדל + הפניקס).
2. **חברות נוספות** — מודולרי, לפי אותו pattern (`companies/<name>/`), ללא שינוי ב-`core/`.
3. **עתידי (לא נבנה עוד)**: OCR_Results / Extracted_Text / Processing_Logs / Appendices / Policies tables, JSON Knowledge Dictionary, שכבת RAG/LLM (chunking, embeddings, vector DB) — הכל נדחה עד שיהיה בהם שימוש קונקרטי.
