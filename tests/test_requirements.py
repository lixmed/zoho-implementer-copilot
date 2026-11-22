from email.message import EmailMessage

import docx
import pytest

from copilot.projects.requirements import blocks_to_units, ingest_dir, load_units, read_blocks

MD = """# Lead capture
Leads come from the website and from referrals. We need to know the budget range of each lead.
- Agents must see the property a deal is about
- Send the agent an email when a hot lead arrives

Thanks
Call me on +1 (555) 123-4567 or mail jane.client@acme-realty.test.
"""


def minimal_pdf(text: str) -> bytes:
    """Hand-built one-page PDF; pypdf repairs the xref offsets on read."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_markdown_sentences_bullets_sections_and_masking(tmp_path):
    (tmp_path / "Kickoff Notes.md").write_text(MD, encoding="utf-8")
    units = ingest_dir(tmp_path)
    texts = [u.text for u in units]
    assert units[0].id == "kickoff-notes:1"
    assert texts[:4] == [
        "Leads come from the website and from referrals.",
        "We need to know the budget range of each lead.",
        "Agents must see the property a deal is about",
        "Send the agent an email when a hot lead arrives",
    ]
    assert all(u.section == "Lead capture" for u in units)
    assert "Thanks" not in texts  # one-word fragment dropped
    joined = " ".join(texts)
    assert "[phone]" in joined and "[email]" in joined
    assert "555" not in joined and "acme-realty" not in joined


def test_ids_are_stable_and_cache_is_used(tmp_path):
    (tmp_path / "a.md").write_text(MD, encoding="utf-8")
    first = [u.id for u in ingest_dir(tmp_path)]
    assert [u.id for u in load_units(tmp_path)] == first
    assert (tmp_path / ".units.json").exists()


def test_email_drops_quotes_and_signature(tmp_path):
    msg = EmailMessage()
    msg["Subject"] = "Change: add viewing feedback"
    msg["From"] = "client@acme-realty.test"
    msg.set_content(
        "Hi,\nWe also need agents to record feedback after each viewing.\n\n"
        "> old quoted text that must not appear\n--\nJane, Acme Realty\n"
    )
    (tmp_path / "cr1.eml").write_bytes(msg.as_bytes())
    units = ingest_dir(tmp_path)
    assert [u.text for u in units] == ["We also need agents to record feedback after each viewing."]
    assert units[0].section == "Change: add viewing feedback"


def test_docx_paragraphs_headings_and_tables(tmp_path):
    d = docx.Document()
    d.add_heading("Deals", level=1)
    d.add_paragraph("Each deal must link to one property. Commission is 2 percent.")
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "REQ-7"
    table.rows[0].cells[1].text = "Managers approve discounts above 5 percent"
    d.save(tmp_path / "scope.docx")
    units = ingest_dir(tmp_path)
    assert [u.text for u in units] == [
        "Each deal must link to one property.",
        "Commission is 2 percent.",
        "REQ-7 | Managers approve discounts above 5 percent",
    ]
    assert units[0].section == "Deals"


def test_pdf_text_with_page_numbers(tmp_path):
    (tmp_path / "rfp.pdf").write_bytes(minimal_pdf("Track every property viewing in the CRM."))
    units = ingest_dir(tmp_path)
    assert units[0].text == "Track every property viewing in the CRM."
    assert units[0].page == 1


def test_unsupported_files_are_skipped(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    (tmp_path / "notes.txt").write_text("Two words here.", encoding="utf-8")
    assert [u.doc for u in ingest_dir(tmp_path)] == ["notes"]
    with pytest.raises(ValueError):
        read_blocks(tmp_path / "logo.png")


def test_blocks_to_units_direct():
    from copilot.projects.requirements import Block

    units = blocks_to_units("d", [Block(text="H", is_heading=True), Block(text="A b. C d.")])
    assert [(u.id, u.text, u.section) for u in units] == [
        ("d:1", "A b.", "H"),
        ("d:2", "C d.", "H"),
    ]
