# Akshar

Akshar rebuilds an English PDF or image as a Gujarati document. It keeps the page size, pictures, rules, stamps, and tables, and it writes Unicode Gujarati with Noto Sans Gujarati embedded in the file. Nothing has to be installed on the computer that opens the result.

## Run

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py
```

Open http://127.0.0.1:8765

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
