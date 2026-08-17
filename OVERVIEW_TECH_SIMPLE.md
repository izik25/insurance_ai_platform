# Enterprise Processing Platform — סקירה טכנית מקוצרת

גרסה מפושטת של [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — אותו מידע, פחות מלל, יותר טבלאות. לפרטי הרקע המלאים (למה הוחלט מה שהוחלט) חזרו למסמך המקורי.

## עקרון-על

כל חברת ביטוח = פלאגין עצמאי תחת `companies/<name>/`, בלי תלות בין חברות. קוד משותף (DB, PDF, OCR, extraction, storage) חי ב-`core/` ולא מכיר חברה ספציפית. **חברה חדשה = תיקייה חדשה בלבד, אין נגיעה ב-`core/`.**

## מבנה תיקיות

```
insurance_ai_platform/
├── main.py                # FastAPI: GET /health + api_router
├── core/
│   ├── config/             # Settings (pydantic-settings)
│   ├── database/           # SQLAlchemy 2.0 models + session
│   ├── extraction/          # טקסט, appendix_number, LLM extraction
│   ├── embeddings/          # multilingual-e5 (מקומי)
│   ├── matching/            # cosine similarity חוצה-חברות
│   ├── ocr/                 # Tesseract
│   ├── pdf_processing/      # PyMuPDF wrapper
│   ├── plugins/              # ABCs + CompanyRegistry
│   └── storage/              # LocalFileStorage
├── api/                    # routes לדשבורד — קריאה בלבד
├── frontend/                # React + Vite + TS, RTL
├── companies/{migdal,phoenix,clal,menorah,directinsurance}/
├── data/raw_documents/<company>/
├── scripts/                 # CLI, ראו טבלה למטה
└── tests/
```

## חוזה הפלאגין

כל חברה מממשת 4 ABCs (`core/plugins/base.py`):

| רכיב | תפקיד |
|---|---|
| `CompanyConfig` | מזהה חברה + הגדרות ספציפיות |
| `BaseDownloader` | `download_all()` — מוריד קבצים |
| `BaseParser` | `extract_text()` — טקסט מוטמע בלבד, לא OCR |
| `BaseExtractor` | `extract_fields()` — שדות מובנים, אחראי על fallback ל-OCR |
| `BaseRules` | `get_ocr_crop_regions()` — אזורי חיתוך ל-OCR |

**להוספת חברה:** `config.py` + 4 המחלקות + `register(registry)` ב-`__init__.py`. שום שינוי ב-`core/`.

## סכמת ה-DB (PostgreSQL)

| טבלה | תפקיד |
|---|---|
| `companies` | חברות ביטוח |
| `documents` | מסמך: חברה, קובץ, domain, appendix_number, עמודים |
| `document_extractions` | שדות שחולצו ע"י LLM (1:1 עם document) — coverage, זכאות, סכומים, תקופות המתנה, חריגים... |
| `document_embeddings` | embedding כ-`ARRAY(Float)` (לא pgvector — לא נדרש בסדר גודל הנוכחי) |
| `document_matches` | התאמה בין שני מסמכים + ציון דמיון + סטטוס |

אין Alembic — הסכמה מנוהלת ע"י `Base.metadata.create_all()`.

## החברות שנבנו

| חברה | מקור נתונים | הגנה שהתמודדנו איתה | מספר נספח מגיע מ- | מסמכים |
|---|---|---|---|---|
| **מגדל** | `my.migdal.co.il` API | אין (API לא-מוגן) | תוכן העמוד (טקסט → OCR → שם קובץ כמוצא אחרון) | 1,053 |
| **הפניקס** | `fnx.co.il` טופס ASP.NET | CloudFront+WAF Bot Control — עקיפה עם Playwright | ישירות מטבלת התוצאות באתר | 1,903 |
| **כלל** | `clalbit.co.il` Angular SPA / Umbraco API | bot-management (Imperva/Akamai) — נדרש session מ-Playwright | JSON של ה-API (`AttachmentNumber`) | 527 |
| **מנורה מבטחים** | `menoramivt.co.il` Next.js API | CAPTCHA אמיתי אחרי ריצוף בקשות — נפתר עם delay שמרני | טקסט חופשי בתוך `policyHeader`, נחלץ ב-regex | 777 |
| **ביטוח ישיר** | `555.co.il` REST API | אין הגנה בכלל, הכי פשוט מבין החמש | כמעט תמיד לא קיים — backfill מה-LLM | 231 |

פרטי המימוש המלאים (כולל היסטוריית הבאגים והחלטות עיצוב ספציפיות) נשארים ב-PROJECT_OVERVIEW.md.

## Pipeline: מקובץ ועד התאמה

```
PDF → extract_text() [+ OCR אם צריך] → LLM (extract_fields, structured output)
    → embedding (multilingual-e5, מבוסס על השדות שחולצו, לא טקסט גולמי)
    → cosine similarity מול מסמכים מחברה אחרת, באותו domain
    → ≥95% = auto_confirmed, מתחת = pending_review
```

**חשוב:** ה-embedding מחושב על השדות שחולצו (לא על הטקסט הגולמי) — כדי שניסוח שונה בין חברות לא ישבש את ההתאמה.

## סקריפטים

| קובץ | מטרה |
|---|---|
| `download_<company>.py` / `sync_<company>.py` | הורדה + אכלוס DB (sync = listing פעם אחת, עם cache) |
| `extract_documents.py` | חילוץ שדות דרך OpenAI Batch API. **תמיד עם `--limit` לבדיקות** — בלי זה רץ שעות על הכל בלי checkpointing |
| `embed_documents.py` | embedding לכל מסמך עם extraction |
| `match_documents.py` | חישוב התאמות חוצות-חברות |

## LLM ו-Embeddings

- **LLM**: OpenAI `gpt-4.1-mini`, לא Anthropic (חסימת אימות זהות ברמת הארגון בחשבון Anthropic של המשתמש). Message Batches API + Structured Outputs.
- **Embeddings**: מודל מקומי חינמי (`multilingual-e5-large`), לא API בתשלום.

## Dashboard

קריאה-בלבד, בלי LLM calls. Backend: FastAPI (`GET /api/documents|extractions/{id}|matches`). Frontend: React+Vite+TS, RTL, בלי ספריית UI.

**נקודה טכנית לזכור:** מזהי מסמך מכילים `/` (למשל `phoenix:phoenix/health/x.pdf`) — לכן route בצד השרת חייב להיות `{document_id:path}`, וקידוד ה-URL בצד הלקוח צריך להיות per-segment.

## 4 עקרונות עיצוב

1. זהות מסמך = חברה + שם קובץ + appendix_number (רשימה) + domain + שיטת חילוץ.
2. דה-דופליקציה לפי content hash, לא URL/שם קובץ.
3. אמון בגוף המקור (מטא-דאטה של האתר) כשקיים; אחרת — קריאת תוכן בפועל, לא ניחוש משם קובץ.
4. התנהגות מנומסת מול אתרים: delays, backoff, retry מוגבל, לא הסלמה אוטומטית.

## מה הבא

1. חברות נוספות (אותו pattern, אין צורך לשנות קוד קיים).
2. בדיקת איכות matching — מדגם ידני בדשבורד.
3. checkpointing מלא ל-extract/embed (כרגע כבר ב-chunks, אבל יש עוד לחזק).
4. timeout ל-OCR כדי שעמוד פגום לא יתקע ריצה שלמה.
5. סיכון ידוע: `sync_menorah/clal/phoenix.py` חולקים דפוס merge שעלול לדרוס backfill — לא תוקן כי לא רלוונטי כרגע (ר' פירוט מלא במסמך המקורי).
6. עתידי, לא נבנה: טבלאות OCR_Results/Extracted_Text/Processing_Logs/Appendices/Policies.
