"""Offline checks for protection, layout, erasure, and PDF reconstruction."""

from __future__ import annotations

import re
import unittest

import numpy as np
import pymupdf

from app.jobs import create_job, load_job
from app.models import DetectedLine, TextBlock
from app.services.translation import protector
from app.services.translation.service import translate_blocks
from app.samples import build_certificate
from app.services.export import export_job
from app.services.layout import build_blocks
from app.services.pipeline import process_job
from app.services.reconstruct import erase_text


class FixedTranslator:
    name = "fixed"

    def translate_batch(self, texts, force=False):
        return ["અનુવાદ" if re.search(r"[A-Za-z]", text) else text for text in texts]


class _EchoTranslator:
    name = "echo"

    def translate_batch(self, texts, force=False):
        return list(texts)


class ProtectorTests(unittest.TestCase):
    def test_identifier_lines_stay_whole(self):
        for text in [
            "SOP No: QA-001",
            "Batch No: BN-2024-118",
            "GSTIN: 24ABCDE1234F1Z5",
            "PAN: ABCDE1234F",
            "Invoice No: INV-2024-015",
            "Serial No: SN-77821",
        ]:
            parts = protector.segment_text(text)
            self.assertEqual([(part.kind, part.value) for part in parts], [("keep", text)])

    def test_tokens_inside_sentences_are_kept(self):
        text = "Write to qa@northwind.example about 99.4% on 12/03/2024."
        kept = [part.value for part in protector.segment_text(text) if part.kind == "keep"]
        self.assertIn("qa@northwind.example", kept)
        self.assertIn("99.4%", kept)
        self.assertIn("12/03/2024", kept)

    def test_brands_and_names_stay_latin(self):
        text = "Share listings on YouTube and Vimeo, then email Mr. James Johnson."
        kept = [part.value for part in protector.segment_text(text) if part.kind == "keep"]
        self.assertTrue(any("YouTube" in value for value in kept))
        self.assertTrue(any("Vimeo" in value for value in kept))
        self.assertTrue(any("James Johnson" in value for value in kept))
        prose = " ".join(part.value for part in protector.segment_text(text) if part.kind == "text")
        self.assertIn("Share", prose)
        self.assertIn("email", prose)

    def test_phone_and_range(self):
        text = "Call +91 79 4000 2210 if assay is 98.0% to 102.0%."
        kept = [part.value for part in protector.segment_text(text) if part.kind == "keep"]
        self.assertTrue(any("4000" in value for value in kept))
        self.assertTrue(any("98.0%" in value and "102.0%" in value for value in kept))


class GlossaryTests(unittest.TestCase):
    def test_page_numbers_and_limits_stay_readable(self):
        page = TextBlock("a", 0, [0, 0, 10, 10], [0, 0, 10, 10], [[0, 0, 10, 10]], "Page 1 of 2")
        limit = TextBlock("b", 0, [0, 0, 10, 10], [0, 0, 10, 10], [[0, 0, 10, 10]], "Not more than 0.50%")
        released = TextBlock("c", 0, [0, 0, 10, 10], [0, 0, 10, 10], [[0, 0, 10, 10]], "RELEASED")
        translate_blocks([page, limit, released], FixedTranslator())
        self.assertEqual(page.translation, "પાનું 1 માંથી 2")
        self.assertIn("0.50%", limit.translation)
        self.assertIn("વધુમાં વધુ", limit.translation)
        self.assertEqual(released.translation, "મુક્ત")


class LayoutTests(unittest.TestCase):
    def test_paragraphs_merge_and_table_cells_stay_separate(self):
        lines = [
            DetectedLine("Certificate of Analysis", [40, 40, 280, 62], 18, bold=True),
            DetectedLine("This certificate confirms that the batch", [40, 80, 320, 94], 11),
            DetectedLine("was tested and released.", [40, 96, 240, 110], 11),
            DetectedLine("Assay", [40, 160, 120, 176], 10),
            DetectedLine("99.4%", [180, 160, 250, 176], 10),
            DetectedLine("Water", [40, 184, 120, 200], 10),
            DetectedLine("0.22%", [180, 184, 250, 200], 10),
        ]
        blocks = build_blocks(lines, 0, 400, 600)
        kinds = [block.kind for block in blocks]
        self.assertIn("heading", kinds)
        self.assertTrue(any(block.kind == "paragraph" and "confirms" in block.text for block in blocks))
        self.assertGreaterEqual(sum(kind == "cell" for kind in kinds), 4)

    def test_nested_title_lines_do_not_share_a_box(self):
        lines = [
            DetectedLine("HoneyXP PURITY & EXCELLENCE", [128, 180, 305, 242], 48),
            DetectedLine("PURITY & EXCELLENCE", [154, 238, 281, 250], 9),
            DetectedLine("PREMIUM RAW", [60, 170, 170, 188], 13),
            DetectedLine("WILD FOREST HONEY", [45, 184, 200, 208], 18),
        ]
        blocks = build_blocks(lines, 0, 320, 420)
        texts = [block.text for block in blocks]
        self.assertTrue(any(text.strip() == "HoneyXP" for text in texts))
        self.assertTrue(any("PURITY" in text and "HoneyXP" not in text for text in texts))
        for left, right in zip(blocks, blocks[1:]):
            shared_x = min(left.draw_bbox[2], right.draw_bbox[2]) - max(left.draw_bbox[0], right.draw_bbox[0])
            shared_y = min(left.draw_bbox[3], right.draw_bbox[3]) - max(left.draw_bbox[1], right.draw_bbox[1])
            if shared_x > 8:
                self.assertLessEqual(shared_y, 0.2)

    def test_short_lines_keep_their_rows_and_the_column(self):
        lines = [
            DetectedLine("To: A", [80, 40, 220, 58], 14),
            DetectedLine("From: B", [80, 62, 260, 80], 14),
            DetectedLine("This is a long paragraph line that spans the column", [80, 140, 900, 158], 14),
            DetectedLine("and continues on the next line of the same paragraph.", [80, 160, 880, 178], 14),
        ]
        blocks = build_blocks(lines, 0, 1000, 800)
        texts = [block.text for block in blocks]
        self.assertIn("To: A", texts)
        self.assertIn("From: B", texts)
        self.assertTrue(any("long paragraph" in text and "continues" in text for text in texts))
        header = next(block for block in blocks if block.text == "To: A")
        self.assertGreater(header.draw_bbox[2], 500)

    def test_column_prose_becomes_paragraphs_not_slivers(self):
        lines = []
        columns = (20, 100, 180)
        copies = (
            ("Scan local news in print today", "and online for success stories."),
            ("Print the invitations neatly", "and hand them to people."),
            ("Identify topics clients care", "about and send them updates."),
        )
        for left, parts in zip(columns, copies):
            top = 40
            for part in parts:
                lines.append(DetectedLine(part, [left, top, left + 68, top + 8], 7))
                top += 9
        blocks = build_blocks(lines, 0, 260, 180)
        prose = [block for block in blocks if "today" in block.text or "neatly" in block.text or "care" in block.text]
        self.assertEqual(len(prose), 3)
        for block in prose:
            self.assertNotEqual(block.kind, "cell")
            self.assertGreater(block.draw_bbox[3] - block.draw_bbox[1], 12)
            self.assertIn("and ", block.text)


class RenderTests(unittest.TestCase):
    def test_display_words_are_translated(self):
        gold = TextBlock(
            "g", 0, [0, 0, 10, 10], [0, 0, 10, 10], [[0, 0, 10, 10]], "GOLD", preserved=True, translation="GOLD"
        )
        experience = TextBlock(
            "e", 0, [0, 0, 10, 10], [0, 0, 10, 10], [[0, 0, 10, 10]], "EXPERIENCE", preserved=True
        )
        translate_blocks([gold, experience], FixedTranslator())
        self.assertEqual(gold.translation, "સુવર્ણ")
        self.assertFalse(gold.preserved)
        self.assertEqual(experience.translation, "અનુભવ")

    def test_hello_and_twitter_convert_without_touching_handles(self):
        from app.services.translation import glossary

        hello = TextBlock("h", 0, [0, 0, 10, 10], [0, 0, 10, 10], [[0, 0, 10, 10]], "Hello")
        sentence = TextBlock("s", 0, [0, 0, 10, 10], [0, 0, 10, 10], [[0, 0, 10, 10]], "Say Hello on Twitter")
        site = TextBlock("u", 0, [0, 0, 10, 10], [0, 0, 10, 10], [[0, 0, 10, 10]], "https://twitter.com/akshar")
        handle = TextBlock("a", 0, [0, 0, 10, 10], [0, 0, 10, 10], [[0, 0, 10, 10]], "Follow @twitter")
        translate_blocks([hello, sentence, site, handle], _EchoTranslator())
        self.assertEqual(hello.translation, "નમસ્તે")
        self.assertEqual(sentence.translation, "Say નમસ્તે on ટ્વિટર")
        self.assertEqual(site.translation, "https://twitter.com/akshar")
        self.assertEqual(handle.translation, "Follow @twitter")
        self.assertEqual(glossary.lookup("Twitter"), "ટ્વિટર")

    def test_mixed_lines_translate_words_and_keep_keepers(self):
        from app.services.translation import glossary

        mail = TextBlock(
            "m",
            0,
            [0, 0, 10, 10],
            [0, 0, 10, 10],
            [[0, 0, 10, 10]],
            "From: james.johnson@ss.com Subject: Changes to magazine distribution",
        )
        body = TextBlock(
            "b",
            0,
            [0, 0, 10, 10],
            [0, 0, 10, 10],
            [[0, 0, 10, 10]],
            "Write to qa@northwind.example and share the PDF on YouTube.",
        )
        translate_blocks([mail], _EchoTranslator())
        self.assertIn("પ્રેષક", mail.translation)
        self.assertIn("વિષય", mail.translation)
        self.assertIn("james.johnson@ss.com", mail.translation)
        self.assertNotIn("From:", mail.translation)
        self.assertEqual(glossary.lookup("Subject"), "વિષય")
        translate_blocks([body], FixedTranslator())
        self.assertIn("qa@northwind.example", body.translation)
        self.assertIn("YouTube", body.translation)
        self.assertIn("PDF", body.translation)
        self.assertIn("અનુવાદ", body.translation)
        self.assertNotRegex(body.translation, r"\bWrite\b")
        self.assertNotRegex(body.translation, r"\bshare\b")

    def test_metallic_letters_are_covered(self):
        image = np.full((120, 240, 3), (236, 224, 198), dtype=np.uint8)
        image[46:62, 40:190] = (214, 168, 48)
        image[68:80, 40:190] = (214, 168, 48)
        cleaned = erase_text(image, [[18, 20, 100, 42]], 120, 60)
        self.assertGreater(int(cleaned[52, 100, 2]), 120)
        self.assertGreater(int(cleaned[73, 100, 2]), 120)

    def test_english_ink_is_removed_and_rules_remain(self):
        image = np.full((80, 220, 3), 255, dtype=np.uint8)
        image[32:42, 50:95] = (10, 10, 10)
        image[48:51, 15:205] = (10, 10, 10)
        cleaned = erase_text(image, [[15, 14, 60, 28]], 110, 40)
        self.assertGreater(int(cleaned[37, 70].mean()), 200)
        self.assertLess(int(cleaned[49, 80].mean()), 40)

    def test_pdf_embeds_gujarati_and_preserves_codes(self):
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "certificate.pdf"
            build_certificate(source)
            job = create_job(source, "certificate.pdf")
            process_job(job.id, translator=FixedTranslator())
            finished = load_job(job.id)
            self.assertEqual(finished.status, "ready")
            self.assertEqual(finished.page_count, 2)
            self.assertGreaterEqual(len(finished.blocks), 8)
            texts = "\n".join(block.translation or "" for block in finished.blocks)
            sources = "\n".join(block.text for block in finished.blocks)
            self.assertIn("QA-001", sources)
            self.assertIn("QA-001", texts)
            self.assertIn("24ABCDE1234F1Z5", texts)
            self.assertIn("release@northwind.example", texts)
            self.assertTrue(any("\u0a80" <= ch <= "\u0aff" for ch in texts))
            from app.services.export import export_job

            pdf_path = export_job(finished, "pdf", 150)
            document = pymupdf.open(pdf_path)
            self.assertEqual(document.page_count, 2)
            extracted = "\n".join(page.get_text("text") for page in document)
            self.assertIn("QA-001", extracted)
            self.assertTrue(any("\u0a80" <= ch <= "\u0aff" for ch in extracted))
            fonts = {item[3] for item in document[0].get_fonts()}
            self.assertTrue(any("Gujarati" in name for name in fonts))
            document.close()


if __name__ == "__main__":
    unittest.main()
