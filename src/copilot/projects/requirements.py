"""Turn client requirement files into addressable units, the basis of traceability.

Every unit gets a stable id like ``kickoff-notes:12``. Plan items cite these ids, so
each item can be traced back to the exact sentence the client wrote.

Supported: .md .txt .eml .docx .pdf
Contact details (emails, phone numbers) are masked in unit text before anything is
shown to the LLM: they are never needed to design a CRM.
"""

from __future__ import annotations

import email
import email.policy
import json
import re
from pathlib import Path

from pydantic import BaseModel

from copilot.orgs.registry import slugify

SUPPORTED = {".md", ".txt", ".eml", ".docx", ".pdf"}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{7,}\d(?![\w])")
BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|[a-z][.)])\s+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*)$")
GREETING_RE = re.compile(
    r"^(hi|hello|hey|dear|good (morning|afternoon|evening))\b[^.!?]{0,30},?$", re.IGNORECASE
)


class Unit(BaseModel):
    id: str
    doc: str
    text: str
    section: str | None = None
    page: int | None = None


class Block(BaseModel):
    """A paragraph-ish chunk before sentence splitting."""

    text: str
    is_heading: bool = False
    page: int | None = None


def mask_contacts(text: str) -> str:
    return PHONE_RE.sub("[phone]", EMAIL_RE.sub("[email]", text))


# ---------- readers: file -> blocks ----------


def _text_blocks(text: str, page: int | None = None) -> list[Block]:
    blocks: list[Block] = []
    para: list[str] = []

    def flush():
        if para:
            blocks.append(Block(text=" ".join(para), page=page))
            para.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif m := MD_HEADING_RE.match(line):
            flush()
            blocks.append(Block(text=m.group(1).strip(), is_heading=True, page=page))
        elif BULLET_RE.match(line):
            flush()  # each bullet is its own requirement-sized block
            para.append(BULLET_RE.sub("", line))
            flush()
        else:
            para.append(line)
    flush()
    return blocks


def _eml_blocks(path: Path) -> list[Block]:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    body_part = msg.get_body(preferencelist=("plain",))
    body = body_part.get_content() if body_part else ""
    kept = []
    for line in body.splitlines():
        if line.strip() == "--":  # signature delimiter
            break
        if line.lstrip().startswith(">"):  # quoted reply
            continue
        if re.match(r"^On .+ wrote:$", line.strip()):
            break
        if GREETING_RE.match(line.strip()):
            continue
        kept.append(line)
    blocks = []
    if subject := msg.get("subject"):
        blocks.append(Block(text=str(subject), is_heading=True))
    return blocks + _text_blocks("\n".join(kept))


def _docx_blocks(path: Path) -> list[Block]:
    import docx  # python-docx

    doc = docx.Document(str(path))
    blocks = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = (p.style.name or "").lower() if p.style is not None else ""
        blocks.append(Block(text=text, is_heading=style.startswith(("heading", "title"))))
    for table in doc.tables:  # requirement matrices are often tables
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(Block(text=" | ".join(dict.fromkeys(cells))))
    return blocks


def _pdf_blocks(path: Path) -> list[Block]:
    from pypdf import PdfReader

    blocks = []
    for i, page in enumerate(PdfReader(str(path)).pages, start=1):
        blocks.extend(_text_blocks(page.extract_text() or "", page=i))
    return blocks


def read_blocks(path: Path) -> list[Block]:
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return _text_blocks(path.read_text(encoding="utf-8", errors="replace"))
    if suffix == ".eml":
        return _eml_blocks(path)
    if suffix == ".docx":
        return _docx_blocks(path)
    if suffix == ".pdf":
        return _pdf_blocks(path)
    raise ValueError(f"unsupported requirement file type: {path.name}")


# ---------- blocks -> units ----------


def blocks_to_units(doc_id: str, blocks: list[Block]) -> list[Unit]:
    units: list[Unit] = []
    section: str | None = None
    for b in blocks:
        if b.is_heading:
            section = mask_contacts(b.text)
            continue
        for sentence in SENTENCE_SPLIT_RE.split(b.text):
            sentence = sentence.strip()
            if len(sentence.split()) < 2:  # skip stray fragments like "Thanks"
                continue
            units.append(
                Unit(
                    id=f"{doc_id}:{len(units) + 1}",
                    doc=doc_id,
                    text=mask_contacts(sentence),
                    section=section,
                    page=b.page,
                )
            )
    return units


def ingest_dir(requirements_dir: Path) -> list[Unit]:
    """Read every supported file (sorted, so ids are stable) and write .units.json."""
    units: list[Unit] = []
    seen: set[str] = set()
    for path in sorted(requirements_dir.iterdir()):
        if path.name.startswith(".") or path.suffix.lower() not in SUPPORTED:
            continue
        doc_id = slugify(path.stem)
        if doc_id in seen:
            doc_id = slugify(f"{path.stem}-{path.suffix[1:]}")
        seen.add(doc_id)
        units.extend(blocks_to_units(doc_id, read_blocks(path)))
    (requirements_dir / ".units.json").write_text(
        json.dumps([u.model_dump() for u in units], indent=2), encoding="utf-8"
    )
    return units


def load_units(requirements_dir: Path) -> list[Unit]:
    cache = requirements_dir / ".units.json"
    newest = max(
        (p.stat().st_mtime for p in requirements_dir.iterdir() if p.suffix.lower() in SUPPORTED),
        default=0.0,
    )
    if not cache.exists() or cache.stat().st_mtime < newest:
        return ingest_dir(requirements_dir)
    return [Unit.model_validate(u) for u in json.loads(cache.read_text(encoding="utf-8"))]
