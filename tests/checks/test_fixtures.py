import re
import zipfile

from iakit import fixture_epub, fixture_pdf, fixtures
from iakit.paths import FIXTURES


def test_committed_fixtures_match_generator():
    assert fixtures.check()["errors"] == []


def test_generation_is_deterministic():
    assert fixture_pdf.build() == fixture_pdf.build()
    assert fixture_epub.build() == fixture_epub.build()


def test_pdf_printed_labels_differ_from_page_order():
    data = (FIXTURES / "pdf/page-labels.pdf").read_bytes()
    assert re.search(rb"/Type /Pages .*?/Count (\d+)", data).group(1) == b"5"
    assert b"/PageLabels" in data and b"/S /r" in data
    labels = [p["printed_label"] for p in fixture_pdf.expected()["pages"]]
    indexes = [str(p["pdf_page_index"]) for p in fixture_pdf.expected()["pages"]]
    assert labels != indexes
    assert data.endswith(b"%%EOF\n")


def test_epub_has_spine_hrefs_and_element_ids():
    with zipfile.ZipFile(FIXTURES / "epub/reflowable.epub") as zf:
        assert zf.namelist()[0] == "mimetype"
        assert zf.read("mimetype") == b"application/epub+zip"
        opf = zf.read("OEBPS/content.opf").decode()
        for entry in fixture_epub.expected()["spine"]:
            assert f'href="{entry["href"]}"' in opf
            body = zf.read(f"OEBPS/{entry['href']}").decode()
            for element_id in entry.get("element_ids", []):
                assert f'id="{element_id}"' in body
