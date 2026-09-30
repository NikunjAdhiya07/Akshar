# Akshar

Akshar rebuilds an English PDF or image as a Gujarati document. It keeps the page size, pictures, rules, stamps, and tables, and it writes Unicode Gujarati with Noto Sans Gujarati embedded in the file. Nothing has to be installed on the computer that opens the result.

## Run

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py
```

Open http://127.0.0.1:8765

## Deploy on Vercel

Akshar can run on Vercel as a **Python FastAPI** serverless function.

1. Push this repo to GitHub (already done for `NikunjAdhiya07/Akshar`).
2. Vercel → **Add New Project** → import that repo.
3. Framework preset: **Other**. Root directory: `.`
4. Add Environment Variables (same as `.env`):
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `SUPABASE_ANON_KEY` (optional)
   - `SUPABASE_STORAGE_BUCKET` = `akshar-documents`
5. Deploy.

What was fixed for Vercel:
- `api/index.py` exports the FastAPI `app`
- `vercel.json` routes all traffic to that function (no empty static 404)
- Job files use `/tmp` on Vercel
- Processing runs **inside** the request (background threads do not work on serverless)

Limits to know:
- Hobby functions time out at **60 seconds**. Prefer single-page images; multi-page PDFs may need **Pro** (`maxDuration` 300 in `vercel.json`).
- Bundle size is large (OCR stack). If the build fails on size, use **Render** with `render.yaml` instead.

## Deploy on Render

**Recommended for heavy OCR / multi-page PDFs.**

1. [render.com](https://render.com) → **New → Blueprint** (or Web Service) → select `NikunjAdhiya07/Akshar`
2. Build: `pip install -r requirements.txt` · Start: `python run.py`
3. Add env vars from `.env`. `PORT` is set by Render automatically.
4. Open the Render URL for the working studio.

Docker: `docker build -t akshar .` then `docker run -p 8765:8765 -e AKSHAR_HOST=0.0.0.0 akshar`

## Supabase (optional cloud sync)

Local job folders still work without cloud. To mirror job metadata and uploads to Supabase:

1. Create a project at [supabase.com](https://supabase.com).
2. Copy `.env.example` to `.env` and fill `SUPABASE_URL` plus `SUPABASE_SERVICE_ROLE_KEY` (Project Settings → API).
3. In Supabase → SQL Editor, run `supabase/schema.sql`.
4. Restart the studio. `GET /api/health` reports whether cloud is connected.

When configured, each save upserts into `akshar_jobs` and uploads the source file to the `akshar-documents` bucket. Cloud failures are logged and never block local processing.

Workflow: upload a PDF, JPG, or PNG, review the side-by-side preview, correct any Gujarati block, then export PDF, PNG, or JPG. Multi-page files are processed one page at a time, and a failed page does not discard the others.

## How a page is rebuilt

1. **Load** the PDF or image. Born-digital pages use the PDF text layer. Scanned pages and images go through OCR.
2. **Detect layout**: paragraphs, headings, headers, footers, and tables.
3. **Translate** English to Gujarati. Emails, URLs, phone numbers, dates, measurements, GSTIN, PAN, and identifiers such as `SOP No: QA-001` stay as written.
4. **Validate** missing text, leftover English, broken Unicode, dropped numbers or dates, and text that had to shrink to fit.
5. **Erase** the original English ink while leaving rules, photos, logos, and stamps.
6. **Draw** Gujarati with MuPDF, which shapes the script and embeds the font.
7. **Export** a searchable PDF, or PNG/JPG at 150, 300, or 600 DPI.

## Swapping engines

OCR and translation are separate from layout and export.

- OCR: `app/services/ocr.py`. Digital PDFs use PyMuPDF. Scans use RapidOCR. If Tesseract and `pytesseract` are installed, Tesseract is used instead.
- Translation: `app/services/translation/google.py`. `get_translator()` is the only construction point. A replacement must implement `translate_batch(texts, force=False)`.
- The exact-phrase glossary is `app/services/translation/glossary.py`.

Translation uses Google Translate when it is available, and MyMemory when Google is rate-limited. It needs a network connection. OCR runs locally.

## Font

`fonts/NotoSansGujarati-Regular.ttf` and `fonts/NotoSansGujarati-Bold.ttf` ship with the app under the SIL Open Font License (`fonts/OFL.txt`). The PDF embeds them. The editor loads them in the browser. No Arun, Shruti, Rekha, or other legacy font is required.

## Tests

```bash
.venv\Scripts\python -m unittest tests.test_core -v
```

The offline tests cover identifier protection, table and paragraph grouping, English-ink removal, and a two-page PDF whose text is searchable Gujarati with the font embedded. Sample certificates are generated in `samples/`.
