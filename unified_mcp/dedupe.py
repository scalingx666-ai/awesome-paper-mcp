"""Cross-source paper merge and de-duplication for the unified gateway.

The matching rules mirror ``paper-radar/paper_radar/dedupe.py``: canonical arXiv
id, DOI, exact/fuzzy normalized title with first-author agreement, title-token
Jaccard, and abstract 4-gram shingle Jaccard. The gateway keeps no persistent
state, so this is an in-request merge — enough to stop the same paper being
returned once per backend.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

_ARXIV_NEW = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(v\d+)?")
_ARXIV_OLD = re.compile(r"\b([a-z][a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_GS_TITLE_TAG = re.compile(r"^\s*\[[A-Za-z][A-Za-z ]{0,11}\]\s*")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
        "is", "of", "on", "or", "the", "to", "via", "with",
    }
)

TITLE_FUZZY = 0.94
TITLE_TOKEN_JACCARD = 0.90
ABSTRACT_JACCARD = 0.90
ABSTRACT_TITLE_FLOOR = 0.45


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def normalize_title(title: str) -> str:
    value = unicodedata.normalize("NFKC", title).lower()
    value = re.sub(r"\$[^$]*\$", " ", value)
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def title_tokens(title: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(normalize_title(title))
        if len(token) > 1 and token not in _STOPWORDS
    }


def abstract_shingles(abstract: str, size: int = 4) -> set[tuple[str, ...]]:
    tokens = _TOKEN_RE.findall(unicodedata.normalize("NFKC", abstract or "").lower())
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def jaccard(left: Iterable[object], right: Iterable[object]) -> float:
    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def author_surnames(authors: Iterable[str]) -> list[str]:
    surnames: list[str] = []
    for author in authors:
        parts = [part for part in re.split(r"\s+", author.strip()) if part]
        if parts:
            surnames.append(re.sub(r"[^\w]", "", parts[-1]).lower())
    return surnames


def first_surname(authors: Iterable[str]) -> str:
    surnames = author_surnames(authors)
    return surnames[0] if surnames else ""


def same_first_author(left: Iterable[str], right: Iterable[str]) -> bool:
    left_surname, right_surname = first_surname(left), first_surname(right)
    return bool(left_surname and right_surname and left_surname == right_surname)


# ---------------------------------------------------------------------------
# Record extraction
# ---------------------------------------------------------------------------


@dataclass
class PaperRecord:
    title: str
    authors: List[str] = field(default_factory=list)
    abstract: str = ""
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    published: str = ""
    sources: List[str] = field(default_factory=list)
    source_ids: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "url": self.url,
            "published": self.published,
            "sources": self.sources,
            "source_ids": self.source_ids,
        }
        return out


@dataclass
class MergeResult:
    papers: List[PaperRecord]
    raw_total: int
    duplicates_removed: int
    reasons: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "papers": [paper.to_dict() for paper in self.papers],
            "total": len(self.papers),
            "raw_total": self.raw_total,
            "duplicates_removed": self.duplicates_removed,
            "dedupe_reasons": self.reasons,
        }


def parse_arxiv_id(raw: Any) -> str:
    """Return ``arxiv:<base id>`` for any arXiv id/URL, else ``""``."""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    value = raw.strip()
    if value.lower().startswith("arxiv://"):
        value = value[len("arxiv://") :]
    elif value.lower().startswith("arxiv:"):
        value = value[len("arxiv:") :]
    elif "://" in value and "arxiv.org" not in value:
        return ""
    if "/abs/" in value:
        value = value.split("/abs/", 1)[1]
    value = value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    match = _ARXIV_NEW.search(value) or _ARXIV_OLD.search(value)
    if not match:
        return ""
    return f"arxiv:{match.group(1).lower()}"


def normalize_doi(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return ""
    value = raw.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    value = value.strip().rstrip(".,;")
    return value if value.startswith("10.") else ""


def _ci_get(mapping: Dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, "", [], {}):
            return value
    return None


def _clean_title(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    value = _GS_TITLE_TAG.sub("", raw).strip().strip("\"'")
    value = re.sub(r"\s+", " ", value).strip()
    return "" if value.lower() in {"no title available", "n/a"} else value


def _as_authors(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(name).strip() for name in raw if str(name).strip()]
    if not isinstance(raw, str):
        return []
    value = raw.strip()
    if " - " in value:  # Google Scholar appends "- venue, year"
        value = value.split(" - ", 1)[0]
    parts: list[str] = []
    for chunk in re.split(r"[;]", value):
        parts.extend(part.strip() for part in chunk.split(",") if part.strip())
    return parts


def _as_text(raw: Any) -> str:
    if isinstance(raw, str):
        cleaned = re.sub(r"\s+", " ", raw).strip()
        return "" if cleaned.lower() in {"no abstract available", "n/a"} else cleaned
    return ""


def _as_date(raw: Any) -> str:
    if raw in (None, ""):
        return ""
    return str(raw)


def _first_source_id(item: Dict[str, Any]) -> str:
    for key in ("paper_id", "id", "versioned_id", "doi"):
        value = _ci_get(item, key)
        if value not in (None, ""):
            return str(value)
    return ""


def record_from_item(source: str, item: Dict[str, Any]) -> Optional[PaperRecord]:
    title = _clean_title(_ci_get(item, "title"))
    if not title:
        return None

    external_ids = _ci_get(item, "external_ids")
    external_ids = external_ids if isinstance(external_ids, dict) else {}

    doi = normalize_doi(_ci_get(item, "doi")) or normalize_doi(
        _ci_get(external_ids, "doi")
    )

    arxiv_id = ""
    for candidate in (
        _ci_get(item, "versioned_id"),
        _ci_get(item, "arxiv_id"),
        _ci_get(item, "id"),
        _ci_get(item, "paper_id"),
        _ci_get(item, "resource_uri"),
        _ci_get(item, "url"),
        _ci_get(item, "pdf_url"),
        _ci_get(external_ids, "arxiv"),
    ):
        arxiv_id = parse_arxiv_id(candidate)
        if arxiv_id:
            break

    url = ""
    for key in ("url", "link", "pdf_url"):
        value = _ci_get(item, key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            url = value
            break

    published = _as_date(
        _ci_get(item, "published", "published_date", "year", "updated_date")
    )
    source_id = _first_source_id(item)

    return PaperRecord(
        title=title,
        authors=_as_authors(_ci_get(item, "authors", "author")),
        abstract=_as_text(_ci_get(item, "abstract", "summary")),
        doi=doi,
        arxiv_id=arxiv_id,
        url=url,
        published=published,
        sources=[source],
        source_ids={source: source_id} if source_id else {},
    )


def _iter_items(payload: Any) -> Iterator[Dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(payload, dict):
        return
    found = False
    for key in ("papers", "results", "data", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
            found = True
            break
        if isinstance(value, dict):
            nested = list(_iter_items(value))
            if nested:
                yield from nested
                found = True
                break
    if found:
        return
    if _ci_get(payload, "error"):
        return
    if _ci_get(payload, "title"):
        yield payload


def records_from_payload(source: str, payload: Any) -> Iterator[PaperRecord]:
    for item in _iter_items(payload):
        record = record_from_item(source, item)
        if record is not None:
            yield record


# ---------------------------------------------------------------------------
# Matching and merging
# ---------------------------------------------------------------------------


def match_reason(left: PaperRecord, right: PaperRecord) -> Optional[str]:
    if left.arxiv_id and left.arxiv_id == right.arxiv_id:
        return "arxiv_id"
    if left.doi and right.doi and left.doi == right.doi:
        return "doi"

    left_title = normalize_title(left.title)
    right_title = normalize_title(right.title)
    if not left_title or not right_title:
        return None
    if left_title == right_title:
        author_ok = same_first_author(left.authors, right.authors)
        if author_ok or not left.authors or not right.authors:
            if author_ok or len(title_tokens(left.title)) >= 4:
                return "title_exact"

    if not same_first_author(left.authors, right.authors):
        return None

    ratio = difflib.SequenceMatcher(None, left_title, right_title).ratio()
    if ratio >= TITLE_FUZZY:
        return "title_fuzzy"
    if jaccard(title_tokens(left.title), title_tokens(right.title)) >= TITLE_TOKEN_JACCARD:
        return "title_tokens"
    if (
        ratio >= ABSTRACT_TITLE_FLOOR
        and jaccard(
            abstract_shingles(left.abstract), abstract_shingles(right.abstract)
        )
        >= ABSTRACT_JACCARD
    ):
        return "abstract_shingles"
    return None


def _prefer_url(existing: str, incoming: str) -> str:
    if not incoming:
        return existing
    if not existing:
        return incoming
    if "arxiv.org" in incoming and "arxiv.org" not in existing:
        return incoming
    if "scholar.google" in existing and "scholar.google" not in incoming:
        return incoming
    return existing


def _merge_into(base: PaperRecord, incoming: PaperRecord) -> None:
    if incoming.abstract and len(incoming.abstract) > len(base.abstract):
        base.abstract = incoming.abstract
    if len(incoming.authors) > len(base.authors):
        base.authors = incoming.authors
    base.doi = base.doi or incoming.doi
    base.arxiv_id = base.arxiv_id or incoming.arxiv_id
    base.url = _prefer_url(base.url, incoming.url)
    base.published = base.published or incoming.published
    for source in incoming.sources:
        if source not in base.sources:
            base.sources.append(source)
    for source, source_id in incoming.source_ids.items():
        base.source_ids.setdefault(source, source_id)


@dataclass
class _Index:
    by_arxiv: Dict[str, int] = field(default_factory=dict)
    by_doi: Dict[str, int] = field(default_factory=dict)
    by_title: Dict[str, List[int]] = field(default_factory=dict)
    by_surname: Dict[str, List[int]] = field(default_factory=dict)

    def add(self, record: PaperRecord, position: int) -> None:
        if record.arxiv_id:
            self.by_arxiv.setdefault(record.arxiv_id, position)
        if record.doi:
            self.by_doi.setdefault(record.doi, position)
        title_key = normalize_title(record.title)
        if title_key:
            self.by_title.setdefault(title_key, []).append(position)
        surname = first_surname(record.authors)
        if surname:
            self.by_surname.setdefault(surname, []).append(position)

    def candidates(self, record: PaperRecord) -> list[int]:
        found: list[int] = []
        if record.arxiv_id and record.arxiv_id in self.by_arxiv:
            return [self.by_arxiv[record.arxiv_id]]
        if record.doi and record.doi in self.by_doi:
            return [self.by_doi[record.doi]]
        title_key = normalize_title(record.title)
        if title_key:
            found.extend(self.by_title.get(title_key, []))
        surname = first_surname(record.authors)
        if surname:
            found.extend(self.by_surname.get(surname, []))
        return list(dict.fromkeys(found))


def merge_records(records: Iterable[PaperRecord]) -> MergeResult:
    papers: List[PaperRecord] = []
    index = _Index()
    reasons: Dict[str, int] = {}
    raw_total = 0

    for record in records:
        raw_total += 1
        matched_position: Optional[int] = None
        matched_reason: Optional[str] = None
        for position in index.candidates(record):
            reason = match_reason(papers[position], record)
            if reason:
                matched_position, matched_reason = position, reason
                break

        if matched_position is None:
            papers.append(record)
            index.add(record, len(papers) - 1)
            continue

        _merge_into(papers[matched_position], record)
        index.add(record, matched_position)
        reasons[matched_reason or "unknown"] = reasons.get(matched_reason or "unknown", 0) + 1

    return MergeResult(
        papers=papers,
        raw_total=raw_total,
        duplicates_removed=raw_total - len(papers),
        reasons=dict(sorted(reasons.items())),
    )


def merge_payloads(payloads: Iterable[Tuple[str, Any]]) -> MergeResult:
    records: list[PaperRecord] = []
    for source, payload in payloads:
        records.extend(records_from_payload(source, payload))
    return merge_records(records)
