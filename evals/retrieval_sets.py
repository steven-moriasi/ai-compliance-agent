"""Build the versioned retrieval sets from the loaded corpus.

Document queries use the Federal Register action line plus the title. The
action line stays in the GovInfo XML because ingestion does not store it.
Gold sections are every stored section of that document except the preamble
summary, which repeats the title and would reward copying it.
"""

import json
import random
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import PolicyStatus
from app.domain.models import Policy, PolicySection
from app.ingestion.federal_register.parse import document_action
from app.services.classifier import cfr_labels

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_SUMMARY = "preamble:summary"


@dataclass(frozen=True)
class CuratedSpec:
    """A hand-written query. The builder fills section ids from the corpus."""

    id: str
    query: str
    category: str
    as_of: date = date(2026, 10, 7)
    section_ref_prefixes: tuple[str, ...] = ()
    document_numbers: tuple[str, ...] = ()
    title_contains: tuple[str, ...] = ()
    forbid_document_numbers: tuple[str, ...] = ()
    negative: bool = False


CURATED: tuple[CuratedSpec, ...] = (
    CuratedSpec("abbr-nox", "NOx", "abbreviation", title_contains=("nitrogen oxides",)),
    CuratedSpec("abbr-voc", "VOC", "abbreviation", title_contains=("volatile organic",)),
    CuratedSpec(
        "abbr-pm25",
        "PM2.5",
        "abbreviation",
        title_contains=("fine particulate matter",),
    ),
    CuratedSpec(
        "abbr-hap",
        "HAP",
        "abbreviation",
        title_contains=("hazardous air pollutant",),
    ),
    CuratedSpec("abbr-naaqs", "NAAQS", "abbreviation", title_contains=("NAAQS",)),
    CuratedSpec(
        "long-nox",
        "nitrogen oxides",
        "abbreviation",
        title_contains=("NOx", "NOX", "nitrogen oxides"),
    ),
    CuratedSpec(
        "long-voc",
        "volatile organic compounds",
        "abbreviation",
        title_contains=("volatile organic", "VOC"),
    ),
    CuratedSpec(
        "long-pm25",
        "fine particulate matter",
        "abbreviation",
        title_contains=("PM2.5", "fine particulate", "particulate matter"),
    ),
    CuratedSpec(
        "long-hap",
        "hazardous air pollutants",
        "abbreviation",
        title_contains=("hazardous air pollutant", "HAP"),
    ),
    CuratedSpec(
        "long-naaqs",
        "national ambient air quality standards",
        "abbreviation",
        title_contains=("NAAQS", "national ambient air quality"),
    ),
    CuratedSpec("section-60-4", "§ 60.4", "section-number", section_ref_prefixes=("§ 60.4",)),
    CuratedSpec(
        "section-52-1770",
        "§ 52.1770",
        "section-number",
        section_ref_prefixes=("§ 52.1770",),
    ),
    CuratedSpec(
        "section-63-7500",
        "§ 63.7500",
        "section-number",
        section_ref_prefixes=("§ 63.7500",),
        negative=True,
    ),
    CuratedSpec(
        "date-before-2023-00017",
        "",
        "date-sensitive",
        as_of=date(2023, 1, 5),
        document_numbers=("2023-00017",),
        forbid_document_numbers=("2023-00017",),
    ),
    CuratedSpec(
        "date-on-2023-00017",
        "",
        "date-sensitive",
        as_of=date(2023, 1, 6),
        document_numbers=("2023-00017",),
    ),
    CuratedSpec(
        "date-before-2023-01505",
        "",
        "date-sensitive",
        as_of=date(2023, 1, 29),
        document_numbers=("2023-01505",),
        forbid_document_numbers=("2023-01505",),
    ),
    CuratedSpec(
        "date-on-2023-01505",
        "",
        "date-sensitive",
        as_of=date(2023, 1, 30),
        document_numbers=("2023-01505",),
    ),
    CuratedSpec("oos-cake", "chocolate cake recipe for a birthday", "out-of-scope", negative=True),
    CuratedSpec(
        "oos-revenue",
        "quarterly revenue forecast for a clothing store",
        "out-of-scope",
        negative=True,
    ),
    CuratedSpec(
        "oos-wifi",
        "reset the wifi password on the office router",
        "out-of-scope",
        negative=True,
    ),
)


def token_overlap(query: str, texts: Sequence[str]) -> float:
    """Share of query tokens that also appear in the gold text.

    Title words make this number high. The retrieval report uses the mean as
    the lexical-overlap bias of the document set.
    """
    query_tokens = set(_TOKEN.findall(query.lower()))
    if not query_tokens:
        return 0.0
    gold: set[str] = set()
    for text in texts:
        gold.update(_TOKEN.findall(text.lower()))
    return len(query_tokens & gold) / len(query_tokens)


def round_robin(groups: Mapping[str, Sequence[str]], count: int, seed: int) -> list[str]:
    """Take documents from each CFR part in turn. The seed only shuffles within a part."""
    rng = random.Random(seed)  # noqa: S311
    pools = {key: list(values) for key, values in groups.items()}
    for values in pools.values():
        rng.shuffle(values)
    keys = sorted(pools)
    picked: list[str] = []
    index = 0
    while len(picked) < count and any(pools.values()):
        pool = pools[keys[index % len(keys)]]
        if pool:
            picked.append(pool.pop())
        index += 1
    return picked


def part_key(references: object) -> str:
    labels = cfr_labels(references)
    for label in labels:
        if label.startswith("40:"):
            return label
    return labels[0] if labels else "other"


def section_ref_matches(section_ref: str, prefixes: Sequence[str]) -> bool:
    """Match a section number without also matching a longer neighbor such as § 60.42."""
    for prefix in prefixes:
        if section_ref == prefix or section_ref.startswith(prefix + "#") or section_ref.startswith(
            prefix + "~"
        ):
            return True
    return False


def load_document_items(
    session: Session,
    cache_dir: Path,
    *,
    count: int,
    seed: int,
    as_of: date,
) -> list[dict[str, object]]:
    rows = session.execute(
        select(
            Policy.id,
            Policy.document_number,
            Policy.title,
            Policy.publication_date,
            Policy.cfr_references,
        ).where(
            Policy.status == PolicyStatus.ACTIVE,
            Policy.document_number.is_not(None),
            Policy.title.is_not(None),
        )
    ).all()
    grouped: dict[str, list[str]] = defaultdict(list)
    by_number: dict[str, tuple[str, str, date | None]] = {}
    for policy_id, number, title, published, references in rows:
        if not isinstance(number, str) or not isinstance(title, str):
            continue
        grouped[part_key(references)].append(number)
        by_number[number] = (str(policy_id), title, published)
    chosen = round_robin(grouped, count, seed)
    items: list[dict[str, object]] = []
    for index, number in enumerate(chosen, start=1):
        policy_id, title, published = by_number[number]
        action = _action(cache_dir, published, number)
        query = f"{action} {title}".strip() if action else title
        sections = _sections(session, policy_id)
        gold = [row for row in sections if row.section_ref != _SUMMARY]
        if not gold:
            continue
        texts = [f"{row.heading or ''} {row.text}" for row in gold]
        items.append(
            _item(
                item_id=f"doc-{index:04d}",
                query=query,
                section_ids=[row.id for row in gold],
                document_numbers=[number],
                category="document",
                source="action+title" if action else "title",
                as_of=as_of,
                lexical_overlap=token_overlap(query, texts),
            )
        )
    return items


def load_curated_items(session: Session, specs: Sequence[CuratedSpec]) -> list[dict[str, object]]:
    rows = _corpus_sections(session)
    items: list[dict[str, object]] = []
    for spec in specs:
        if spec.negative and not spec.section_ref_prefixes:
            items.append(
                _item(
                    item_id=spec.id,
                    query=spec.query,
                    section_ids=[],
                    document_numbers=[],
                    category=spec.category,
                    source="curated",
                    as_of=spec.as_of,
                    lexical_overlap=0.0,
                    forbid_document_numbers=list(spec.forbid_document_numbers),
                )
            )
            continue
        matched = [
            row
            for row in rows
            if _spec_matches(spec, row.section_ref, row.document_number or "", row.title)
        ]
        if spec.negative:
            if matched:
                raise SystemExit(f"{spec.id} was marked absent but matched {len(matched)} sections")
            items.append(
                _item(
                    item_id=spec.id,
                    query=spec.query,
                    section_ids=[],
                    document_numbers=[],
                    category=spec.category,
                    source="curated",
                    as_of=spec.as_of,
                    lexical_overlap=0.0,
                )
            )
            continue
        if spec.forbid_document_numbers and not spec.query:
            title = _title(session, spec.forbid_document_numbers[0])
            items.append(
                _item(
                    item_id=spec.id,
                    query=title,
                    section_ids=[],
                    document_numbers=[],
                    category=spec.category,
                    source="curated",
                    as_of=spec.as_of,
                    lexical_overlap=0.0,
                    forbid_document_numbers=list(spec.forbid_document_numbers),
                )
            )
            continue
        if not matched:
            raise SystemExit(f"{spec.id} matched no sections")
        query = spec.query or _title(session, spec.document_numbers[0])
        numbers = sorted({row.document_number for row in matched if row.document_number})
        texts = [f"{row.heading or ''} {row.text}" for row in matched]
        items.append(
            _item(
                item_id=spec.id,
                query=query,
                section_ids=[row.id for row in matched],
                document_numbers=numbers,
                category=spec.category,
                source="curated",
                as_of=spec.as_of,
                lexical_overlap=token_overlap(query, texts),
            )
        )
    return items


def write_jsonl(path: Path, items: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class _Match:
    id: str
    section_ref: str
    heading: str | None
    text: str
    document_number: str | None
    title: str | None = None


def _item(
    *,
    item_id: str,
    query: str,
    section_ids: list[str],
    document_numbers: list[str],
    category: str,
    source: str,
    as_of: date,
    lexical_overlap: float,
    forbid_document_numbers: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": item_id,
        "query": query,
        "section_ids": section_ids,
        "document_numbers": document_numbers,
        "category": category,
        "source": source,
        "as_of": as_of.isoformat(),
        "lexical_overlap": round(lexical_overlap, 6),
    }
    if forbid_document_numbers:
        payload["forbid_document_numbers"] = forbid_document_numbers
    return payload


def _action(cache_dir: Path, published: date | None, document_number: str) -> str:
    if published is None:
        return ""
    path = cache_dir / published.isoformat() / "issue.xml"
    if not path.is_file():
        return ""
    return document_action(path.read_bytes(), document_number)


def _sections(session: Session, policy_id: str) -> list[PolicySection]:
    return list(
        session.scalars(
            select(PolicySection)
            .where(PolicySection.policy_id == policy_id)
            .order_by(PolicySection.position)
        ).all()
    )


def _title(session: Session, document_number: str) -> str:
    title = session.scalar(
        select(Policy.title).where(Policy.document_number == document_number).limit(1)
    )
    if not isinstance(title, str) or not title.strip():
        raise SystemExit(f"{document_number} has no title")
    return title


def _corpus_sections(session: Session) -> list[_Match]:
    rows = session.execute(
        select(
            PolicySection.id,
            PolicySection.section_ref,
            PolicySection.heading,
            PolicySection.text,
            Policy.document_number,
            Policy.title,
        )
        .join(Policy, Policy.id == PolicySection.policy_id)
        .where(Policy.status == PolicyStatus.ACTIVE, PolicySection.section_ref != _SUMMARY)
    ).all()
    loaded: list[_Match] = []
    for section_id, section_ref, heading, text, number, title in rows:
        if not isinstance(section_id, str) or not isinstance(section_ref, str):
            continue
        loaded.append(
            _Match(
                id=section_id,
                section_ref=section_ref,
                heading=heading if isinstance(heading, str) else None,
                text=text if isinstance(text, str) else "",
                document_number=number if isinstance(number, str) else None,
                title=title if isinstance(title, str) else None,
            )
        )
    return loaded


def _spec_matches(spec: CuratedSpec, section_ref: str, number: str, title: object) -> bool:
    if spec.section_ref_prefixes and not section_ref_matches(
        section_ref, spec.section_ref_prefixes
    ):
        return False
    if spec.document_numbers and number not in spec.document_numbers:
        return False
    if spec.title_contains:
        folded = title.lower() if isinstance(title, str) else ""
        if not any(phrase.lower() in folded for phrase in spec.title_contains):
            return False
    if spec.section_ref_prefixes or spec.document_numbers or spec.title_contains:
        return True
    return False
