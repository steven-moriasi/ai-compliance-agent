"""Split a Federal Register document into citable sections.

Preamble headings keep their own outline labels (`preamble:II.B`). Regulatory
text keeps the section number printed in the document (`§ 52.1570`). Both are
stable enough to survive a re-ingest of the same XML.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
from defusedxml.ElementTree import fromstring  # type: ignore[import-untyped]

from app.ingestion.federal_register.normalize import format_subscript, normalize_text
from app.ingestion.federal_register.records import ParsedSection

_DOCUMENT_TAGS = {"RULE", "PRORULE", "NOTICE"}
_FRONT_MATTER = {
    "SUM": "summary",
    "EFFDATE": "dates",
    "ADD": "addresses",
    "FURINF": "contact",
}
_OUTLINE = re.compile(r"^([IVXLC]+|\d+|[A-Z])[.)]\s+")
_SKIP = {"PRTPAGE", "STARS"}


class FederalRegisterParseError(Exception):
    """The body could not be read as a Federal Register document."""


def parse_document_body(payload: bytes, document_number: str | None = None) -> list[ParsedSection]:
    if looks_like_html(payload):
        raise FederalRegisterParseError("full text response was an HTML access page")
    if looks_like_xml(payload):
        return parse_federal_register_xml(payload, document_number=document_number)
    return parse_plain_text(payload.decode("utf-8", errors="replace"))


def parse_federal_register_xml(
    payload: bytes,
    document_number: str | None = None,
) -> list[ParsedSection]:
    if looks_like_html(payload):
        raise FederalRegisterParseError("full text response was an HTML access page")
    try:
        root = fromstring(payload)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise FederalRegisterParseError("Federal Register XML could not be parsed") from exc
    if root.tag == "FEDREG":
        if document_number is None:
            raise FederalRegisterParseError("issue XML requires a document number")
        extracted = extract_document_xml(payload, document_number)
        if extracted is None:
            raise FederalRegisterParseError(f"{document_number} was not in the issue XML")
        return parse_federal_register_xml(extracted, document_number=document_number)
    if root.tag not in _DOCUMENT_TAGS:
        raise FederalRegisterParseError(f"unsupported Federal Register root {root.tag}")
    return _unique_refs(_parse_document(root))


def extract_document_xml(issue_xml: bytes, document_number: str) -> bytes | None:
    """Return one RULE, PRORULE, or NOTICE element from a GovInfo issue file."""
    try:
        root = fromstring(issue_xml)
    except (ET.ParseError, DefusedXmlException):
        return None
    if root.tag in _DOCUMENT_TAGS:
        return _serialize(root)
    for element in root.iter():
        if element.tag not in _DOCUMENT_TAGS:
            continue
        for node in element.iter("FRDOC"):
            if _document_number_in_text("".join(node.itertext()), document_number):
                return _serialize(element)
    return None


def _serialize(element: ET.Element) -> bytes:
    payload = ET.tostring(element, encoding="utf-8", xml_declaration=True)
    if isinstance(payload, bytes):
        return payload
    raise FederalRegisterParseError("serialized Federal Register XML was not bytes")


def parse_plain_text(text: str) -> list[ParsedSection]:
    """Fallback for raw_text_url when the XML body is unavailable."""
    sections: list[ParsedSection] = []
    heading: str | None = None
    ref = "document"
    paragraphs: list[str] = []

    def flush() -> None:
        nonlocal paragraphs, heading, ref
        if paragraphs:
            sections.append(ParsedSection(ref, heading, tuple(paragraphs)))
        paragraphs = []

    for block in re.split(r"\n\s*\n", text):
        cleaned = normalize_text(block)
        if not cleaned:
            continue
        label = _OUTLINE.match(cleaned)
        if label and len(cleaned.split()) < 30:
            flush()
            heading = cleaned
            ref = f"preamble:{label.group(1)}"
            continue
        paragraphs.append(cleaned)
    flush()
    return _unique_refs(sections)


def looks_like_html(payload: bytes) -> bool:
    sample = payload.lstrip().lower()[:200]
    return sample.startswith((b"<!doctype html", b"<html"))


def looks_like_xml(payload: bytes) -> bool:
    sample = payload.lstrip()[:200].lower()
    return sample.startswith((b"<?xml", b"<rule", b"<prorule", b"<notice", b"<fedreg"))


def _parse_document(rule: ET.Element) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    preamble = rule.find("PREAMB")
    if preamble is not None:
        sections.extend(_parse_front_matter(preamble))
    supplement = rule.find("SUPLINF")
    if supplement is not None:
        sections.extend(_parse_supplement(supplement))
    sections.extend(_parse_regtext(rule))
    return sections


def _parse_front_matter(preamble: ET.Element) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    for child in list(preamble):
        ref_name = _FRONT_MATTER.get(child.tag)
        if ref_name is None:
            continue
        paragraphs = _paragraphs(child)
        if paragraphs:
            sections.append(ParsedSection(f"preamble:{ref_name}", None, paragraphs))
    return sections


def _parse_supplement(supplement: ET.Element) -> list[ParsedSection]:
    state = _Outline()
    _walk_supplement(supplement, state)
    state.flush()
    return state.sections


def _parse_regtext(regulatory: ET.Element) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    for section in regulatory.iter("SECTION"):
        sectno = section.find("SECTNO")
        subject = section.find("SUBJECT")
        ref = _section_ref("".join(sectno.itertext()) if sectno is not None else "section")
        heading = normalize_text(_render(subject)) if subject is not None else None
        paragraphs = [
            normalize_text(_render(child))
            for child in list(section)
            if child.tag not in {"SECTNO", "SUBJECT"}
            and normalize_text(_render(child))
        ]
        if not paragraphs and heading:
            paragraphs = [heading]
        if paragraphs:
            sections.append(
                ParsedSection(ref, heading[:240] if heading else None, tuple(paragraphs))
            )
    return sections


def _walk_supplement(element: ET.Element, state: "_Outline") -> None:
    for child in list(element):
        if child.tag == "HD":
            _start_heading(state, child)
        elif child.tag == "P":
            paragraph = normalize_text(_render(child))
            if paragraph and state.ref is not None:
                state.paragraphs.append(paragraph)
        elif child.tag in _SKIP or child.tag == "SECTION":
            continue
        else:
            _walk_supplement(child, state)


def _start_heading(state: "_Outline", heading: ET.Element) -> None:
    source = heading.get("SOURCE") or ""
    if not source.startswith("HD"):
        return
    text = normalize_text(_render(heading))
    if not text:
        return
    label = _outline_label(text)
    if source == "HD1":
        state.hd1, state.hd2, state.hd3 = label, None, None
    elif source == "HD2":
        state.hd2, state.hd3 = label, None
    elif source == "HD3":
        state.hd3 = label
    else:
        return
    parts = [part for part in (state.hd1, state.hd2, state.hd3) if part]
    state.flush()
    state.ref = "preamble:" + ".".join(parts)
    state.heading = text[:240]


@dataclass
class _Outline:
    hd1: str | None = None
    hd2: str | None = None
    hd3: str | None = None
    ref: str | None = None
    heading: str | None = None
    paragraphs: list[str] = field(default_factory=list)
    sections: list[ParsedSection] = field(default_factory=list)

    def flush(self) -> None:
        if self.ref and self.paragraphs:
            self.sections.append(ParsedSection(self.ref, self.heading, tuple(self.paragraphs)))
        self.paragraphs = []


def _paragraphs(element: ET.Element) -> tuple[str, ...]:
    paragraphs = [normalize_text(_render(paragraph)) for paragraph in element.iter("P")]
    return tuple(paragraph for paragraph in paragraphs if paragraph)


def _section_ref(raw: str) -> str:
    cleaned = raw.replace("\u2009", " ").replace("\u00a0", " ").replace("\u2007", " ")
    cleaned = normalize_text(cleaned)
    return cleaned[:120] or "section"


def _outline_label(heading: str) -> str:
    match = _OUTLINE.match(heading)
    if match:
        return match.group(1)
    slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
    return slug[:48] or "section"


def _render(element: ET.Element) -> str:
    parts: list[str] = []

    def add(text: str, *, attach: bool = False) -> None:
        if not text:
            return
        if attach and parts:
            parts[-1] = parts[-1].rstrip()
        parts.append(text)

    def walk(node: ET.Element) -> None:
        if node.text:
            add(node.text)
        for child in list(node):
            if child.tag in _SKIP:
                pass
            elif _is_subscript(child):
                add(format_subscript_text(child), attach=True)
            else:
                walk(child)
            if child.tail:
                add(child.tail)

    walk(element)
    return "".join(parts)


def _is_subscript(element: ET.Element) -> bool:
    if element.tag in {"sub", "inf"}:
        return True
    return element.tag == "E" and element.get("T") == "52"


def format_subscript_text(element: ET.Element) -> str:
    return format_subscript("".join(element.itertext()))


def _document_number_in_text(text: str, document_number: str) -> bool:
    pattern = rf"(?<![\w-]){re.escape(document_number)}(?![\w-])"
    return re.search(pattern, text) is not None


def _unique_refs(sections: list[ParsedSection]) -> list[ParsedSection]:
    seen: dict[str, int] = {}
    unique: list[ParsedSection] = []
    for section in sections:
        seen[section.section_ref] = seen.get(section.section_ref, 0) + 1
        count = seen[section.section_ref]
        ref = section.section_ref if count == 1 else f"{section.section_ref}~{count}"
        unique.append(ParsedSection(ref[:120], section.heading, section.paragraphs))
    return unique
