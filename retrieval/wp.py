"""Read-only keyword retrieval."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from corpus.common import ID_PATTERN, bounds, pdf_relative

from retrieval.ranking import document_filters, lexical
from retrieval.ri import resolve

NOTICE = "Provisional extraction; verify quotations, equations and numbers in the original PDF."
JEL_STATUSES = (
    "portal_sourced",
    "candidate_found_needs_review",
    "not_found_unconfirmed",
)


class RetrievalError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise RetrievalError(
            "invalid_argument", f"{name} must be an integer from {low} to {high}."
        )


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(ID_PATTERN, value):
        raise RetrievalError(
            "invalid_id", "Use a corpus ID such as dt-2026-001, moneda-196-02 or ri-2026-09-es."
        )


def clip(text, limit):
    text = text or ""
    return {
        "text": text[:limit],
        "truncated": len(text) > limit,
        "total_characters": len(text),
    }


def warning_summary(value):
    labels = set()

    def walk(v):
        if isinstance(v, str):
            labels.add(v)
        elif isinstance(v, list):
            for item in v:
                walk(item)
        elif isinstance(v, dict):
            if "warning" in v:
                walk(v["warning"])
            else:
                for k, item in v.items():
                    if k != "notice":
                        walk(item)

    walk(value)
    return {
        "labels": [s[:240] for s in sorted(labels)[:20]],
        "distinct_count": len(labels),
        "truncated": len(labels) > 20 or any(len(s) > 240 for s in labels),
    }


def page_url(url, page):
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    return urlunsplit(parts._replace(fragment=f"page={page}"))


class Corpus:
    def __init__(self, root, database=None, renderer=None):
        self.root = Path(root).resolve()
        self.database = (
            Path(database).resolve() if database else self.root / "data/db/bcrp.sqlite"
        )
        self.renderer = (
            renderer or shutil.which("pdftoppm") or "pdftoppm"
        )

    @contextmanager
    def connection(self):
        c = None
        try:
            c = sqlite3.connect(
                self.database.as_uri() + "?mode=ro", uri=True, timeout=3
            )
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            if c.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RetrievalError(
                    "unsupported_database",
                    "Expected corpus database schema version 1.",
                )
            c.execute("SELECT id FROM documents LIMIT 1")
            yield c
        except sqlite3.Error as exc:
            raise RetrievalError(
                "database_unavailable",
                "Cannot read the corpus database; check its path and integrity.",
            ) from exc
        finally:
            if c is not None:
                c.close()

    def _document(self, c, document_id):
        identifier(document_id)
        row = c.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise RetrievalError(
                "paper_not_found", f"Paper {document_id} is not in this database."
            )
        return dict(row)

    def _summary(self, c, d):
        meta = json.loads(d["metadata_json"] or "{}")
        return {
            "document_id": d["id"],
            "collection": d["collection"],
            "issue_number": meta.get("issue_number"),
            "publication_date": meta.get("publication_date"),
            "physical_page_range": meta.get("physical_page_range"),
            "printed_page_range": meta.get("printed_page_range"),
            "field_status": meta.get("field_status", {}),
            **({"parent_document_id": meta.get("parent_id"),
                "edition_id": meta.get("edition_id"),
                "box_boundary_status": meta.get("box_boundary_status"),
                "publication_date_precision": meta.get("publication_date_precision"),
                "publication_date_status": meta.get("date_status")}
               if d["id"].startswith("ri-") else {}),
            "title": d["title"],
            "year": d["year"],
            "authors": [
                r[0]
                for r in c.execute(
                    "SELECT name FROM authors WHERE doc=? ORDER BY position", (d["id"],)
                )
            ],
            "document_language": d["language"],
            "source_url": d["source_url"],
            "physical_page_count": meta.get("pdf", {}).get("page_count"),
            "text_state": d["state"],
            "exclusion_reason": d["reason"],
            "quality_status": d["quality_status"],
            "jel_status": d["jel_status"],
            "warnings": warning_summary(json.loads(d["warnings_json"] or "{}")),
        }

    def resolve_report(self, report_id=None, language=None, as_of=None):
        with self.connection() as c:
            try:
                return resolve(c, report_id, language, as_of)
            except ValueError as exc:
                raise RetrievalError("invalid_argument", str(exc)) from exc

    def search(
        self,
        query,
        *,
        year_from=None,
        year_to=None,
        author=None,
        language=None,
        jel_status=None,
        jel=None,
        include_candidates=False,
        limit=5,
        kind="all",
        collection=None,
        issue=None,
        report_id=None,
    ):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            raise RetrievalError(
                "invalid_query", "Use 1–500 characters of keywords or quoted phrases."
            )
        if not re.search(r"\w", query):
            raise RetrievalError(
                "invalid_query", "Query must contain a word or number."
            )
        integer(limit, 1, 10, "limit")
        if type(include_candidates) is not bool:
            raise RetrievalError(
                "invalid_argument", "include_candidates must be boolean."
            )
        for name, v in (("year_from", year_from), ("year_to", year_to)):
            if v is not None:
                integer(v, 1, 9999, name)
        if year_from and year_to and year_from > year_to:
            raise RetrievalError(
                "invalid_argument", "year_from must not exceed year_to."
            )
        if language not in (None, "es", "en") or jel_status not in (
            None,
            *JEL_STATUSES,
        ):
            raise RetrievalError(
                "invalid_argument", "Invalid document language or JEL status."
            )
        if author is not None and (
            not isinstance(author, str) or not 1 <= len(author.strip()) <= 200
        ):
            raise RetrievalError(
                "invalid_argument", "author must contain 1–200 characters."
            )
        if jel is not None and (
            not isinstance(jel, str) or not re.fullmatch(r"[A-Za-z]\d{1,2}", jel)
        ):
            raise RetrievalError(
                "invalid_argument", "Use an exact JEL code, for example E52."
            )
        if kind not in ("all", "passage", "metadata"):
            raise RetrievalError(
                "invalid_argument", "kind must be all, passage or metadata."
            )
        filters = {
            "year_from": year_from,
            "year_to": year_to,
            "author": author,
            "language": language,
            "jel_status": jel_status,
            "jel": jel,
            "include_candidates": include_candidates,
            "collection": collection,
            "issue": issue,
        }
        if report_id is not None:
            filters["report_id"] = report_id
        try:
            document_filters(**filters)
        except ValueError as exc:
            raise RetrievalError("invalid_argument", str(exc)) from exc
        with self.connection() as c:
            if report_id is not None:
                scoped = self._document(c, report_id)
                if scoped["collection"] != "inflation_report":
                    raise RetrievalError("invalid_argument", "Search scope must be a full inflation report.")
            try:
                rows = lexical(c, query, filters, kind, limit + 1)
            except (ValueError, sqlite3.OperationalError) as exc:
                if isinstance(exc, RetrievalError):
                    raise
                raise RetrievalError(
                    "invalid_query",
                    "Invalid query/filter; check phrase quotes and candidate-JEL opt-in.",
                ) from exc
            out = {
                "passages": [],
                "metadata_matches": [],
                "has_more": len(rows) > limit,
                "returned": min(len(rows), limit),
                "limit": limit,
                "notice": NOTICE,
            }
            if report_id is not None:
                out["report_scope"] = report_id
            for hit in rows[:limit]:
                d = self._document(c, hit["id"])
                item = self._summary(c, d)
                item.update(match_type=hit["match_type"], pdf_page=hit["pdf_page"])
                if hit["match_type"] == "passage":
                    item.update(
                        chunk_ordinal=hit["ordinal"],
                        section=hit["section"],
                        passage=clip(hit["passage"], 2200),
                        page_warnings=warning_summary(hit["page_warnings"]),
                        pdf_page_url=page_url(d["source_url"], hit["pdf_page"]),
                    )
                    out["passages"].append(item)
                else:
                    item["snippet"] = clip(hit["snippet"], 600)
                    item["citation_notice"] = (
                        "Metadata match only; no physical PDF page attribution."
                    )
                    out["metadata_matches"].append(item)
            return out

    def paper(self, document_id):
        with self.connection() as c:
            d = self._document(c, document_id)
            out = self._summary(c, d)
            out["alternate_metadata"] = [
                {"language": r[0], "title": r[1], "abstract": clip(r[2], 2000)}
                for r in c.execute(
                    "SELECT language,title,abstract FROM alternate_metadata WHERE doc=? ORDER BY language",
                    (document_id,),
                )
            ]
            out["jel"] = [
                dict(r)
                for r in c.execute(
                    "SELECT DISTINCT code,source,status,page AS pdf_page FROM jel WHERE doc=? ORDER BY source,code,page",
                    (document_id,),
                )
            ]
            out["notice"] = NOTICE
            return out

    def _page(self, c, d, page):
        integer(page, 1, 10000, "pdf_page")
        count = json.loads(d["metadata_json"] or "{}").get("pdf", {}).get("page_count")
        if count is not None and page > count:
            raise RetrievalError(
                "page_not_found",
                f"Physical PDF page {page} exceeds page count {count}.",
            )
        if d["state"] != "indexed":
            raise RetrievalError(
                "text_excluded",
                f"Text excluded: {d['reason'] or d['state']}. Use the original PDF page.",
            )
        row = c.execute(
            "SELECT * FROM pages WHERE doc=? AND page=?", (d["id"], page)
        ).fetchone()
        if row is None:
            raise RetrievalError(
                "page_not_found", "No indexed record for this physical page."
            )
        return dict(row)

    def passage(
        self, document_id, pdf_page, chunk_ordinal=None, offset=0, max_characters=6000
    ):
        integer(offset, 0, 10000000, "offset")
        integer(max_characters, 100, 12000, "max_characters")
        with self.connection() as c:
            d = self._document(c, document_id)
            p = self._page(c, d, pdf_page)
            text = p["raw_markdown"] or ""
            if chunk_ordinal is not None:
                integer(chunk_ordinal, 0, 10000, "chunk_ordinal")
                row = c.execute(
                    "SELECT text FROM chunks WHERE doc=? AND page=? AND ordinal=?",
                    (document_id, pdf_page, chunk_ordinal),
                ).fetchone()
                if row is None:
                    raise RetrievalError(
                        "passage_not_found",
                        "No chunk with that ordinal on this physical page.",
                    )
                text = row[0]
            if offset > len(text):
                raise RetrievalError("invalid_argument", "offset exceeds text length.")
            end = min(offset + max_characters, len(text))
            return {
                **self._summary(c, d),
                "pdf_page": pdf_page,
                "chunk_ordinal": chunk_ordinal,
                "text": text[offset:end],
                "offset": offset,
                "next_offset": end if end < len(text) else None,
                "total_characters": len(text),
                "empty_page": not text.strip(),
                "page_warnings": warning_summary(
                    json.loads(p["warnings_json"] or "[]")
                ),
                **({"page_text_scope": "owned_recuadro_text" if "-box-" in document_id else
                    ("owned_report_passage" if chunk_ordinal is not None else "full_report_page_context")}
                   if document_id.startswith("ri-") else {}),
                "extraction_method": p["method"],
                "pdf_page_url": page_url(d["source_url"], pdf_page),
                "notice": NOTICE,
            }

    def context(self, document_id, pdf_page, radius=1, max_characters_per_page=3000):
        integer(radius, 0, 2, "radius")
        integer(max_characters_per_page, 100, 6000, "max_characters_per_page")
        with self.connection() as c:
            d = self._document(c, document_id)
            self._page(c, d, pdf_page)
            count = c.execute(
                "SELECT max(page) FROM pages WHERE doc=?", (document_id,)
            ).fetchone()[0]
        minimum = 1
        if document_id.startswith("moneda-") or "-box-" in document_id:
            minimum, count = bounds(json.loads(d["metadata_json"]))
        lo, hi = max(minimum, pdf_page - radius), min(count, pdf_page + radius)
        return {
            "document_id": document_id,
            "center_pdf_page": pdf_page,
            "start_pdf_page": lo,
            "end_pdf_page": hi,
            "pages": [
                self.passage(document_id, n, max_characters=max_characters_per_page)
                for n in range(lo, hi + 1)
            ],
            "notice": NOTICE,
        }

    def provenance(self, document_id, offset=0, max_characters=8000):
        integer(offset, 0, 10000000, "offset")
        integer(max_characters, 100, 12000, "max_characters")
        with self.connection() as c:
            d = self._document(c, document_id)
            record = {
                "document": d,
                "authors": [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM authors WHERE doc=? ORDER BY position",
                        (document_id,),
                    )
                ],
                "jel": [
                    dict(r)
                    for r in c.execute("SELECT * FROM jel WHERE doc=?", (document_id,))
                ],
                "alternate_metadata": [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM alternate_metadata WHERE doc=? ORDER BY language",
                        (document_id,),
                    )
                ],
                "source_reports": [
                    dict(r)
                    for r in c.execute("SELECT * FROM source_reports ORDER BY path")
                ],
            }
            text = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if offset > len(text):
            raise RetrievalError(
                "invalid_argument", "offset exceeds provenance length."
            )
        end = min(offset + max_characters, len(text))
        return {
            "document_id": document_id,
            "format": "JSON text slice; concatenate slices to parse",
            "text": text[offset:end],
            "offset": offset,
            "next_offset": end if end < len(text) else None,
            "total_characters": len(text),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }

    def pdf_page(self, document_id, pdf_page, pixels=1600):
        integer(pdf_page, 1, 10000, "pdf_page")
        integer(pixels, 800, 2400, "pixels")
        with self.connection() as c:
            d = self._document(c, document_id)
            summary = self._summary(c, d)
            meta = json.loads(d["metadata_json"] or "{}")
            p = c.execute(
                "SELECT warnings_json FROM pages WHERE doc=? AND page=?",
                (document_id, pdf_page),
            ).fetchone()
        try:
            expected = Path(pdf_relative(meta))
            if document_id.startswith(("moneda-", "ri-")):
                lo, hi = bounds(meta)
                if not lo <= pdf_page <= hi:
                    raise ValueError("Physical page outside the verified document range")
        except ValueError as exc:
            raise RetrievalError("page_not_found", str(exc)) from exc
        if d["pdf_path"] != expected.as_posix():
            raise RetrievalError(
                "unsafe_path", "PDF path is not the canonical corpus path."
            )
        path = self.root / expected
        if path.is_symlink() or path.resolve() != path:
            raise RetrievalError("unsafe_path", "Symlinked PDF paths are not allowed.")
        try:
            if path.stat().st_size > 128 * 1024 * 1024:
                raise RetrievalError(
                    "pdf_too_large", "PDF exceeds the 128 MiB safety limit."
                )
            with path.open("rb") as stream:
                pdf_bytes = stream.read(128 * 1024 * 1024 + 1)
            if len(pdf_bytes) > 128 * 1024 * 1024:
                raise RetrievalError("pdf_too_large", "PDF exceeds 128 MiB.")
            digest = hashlib.sha256(pdf_bytes).hexdigest()
            if digest != meta.get("pdf", {}).get("sha256"):
                raise RetrievalError(
                    "pdf_changed",
                    "PDF hash differs from the indexed source; refresh the database before citing it.",
                )
            count = meta.get("pdf", {}).get("page_count")
            if type(count) is not int or not 1 <= pdf_page <= count:
                raise RetrievalError(
                    "page_not_found",
                    "Physical PDF page is outside the recorded source page count.",
                )
            with tempfile.TemporaryDirectory(prefix="bcrp-page-") as temp:
                prefix = Path(temp) / "page"
                verified_pdf = Path(temp) / "source.pdf"
                verified_pdf.write_bytes(pdf_bytes)
                subprocess.run(
                    [
                        self.renderer,
                        "-f",
                        str(pdf_page),
                        "-l",
                        str(pdf_page),
                        "-singlefile",
                        "-scale-to",
                        str(pixels),
                        "-png",
                        str(verified_pdf),
                        str(prefix),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                image = prefix.with_suffix(".png").read_bytes()
            if len(image) > 4 * 1024 * 1024 or not image.startswith(
                b"\x89PNG\r\n\x1a\n"
            ):
                raise RetrievalError(
                    "render_failed",
                    "Rendered page exceeds 4 MiB or is not PNG; try fewer pixels.",
                )
        except FileNotFoundError as exc:
            raise RetrievalError(
                "file_unavailable",
                "PDF or pdftoppm is missing; check corpus and Poppler installation.",
            ) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise RetrievalError(
                "render_failed", "Could not render the original page within 30 seconds."
            ) from exc
        return {
            **summary,
            "pdf_page": pdf_page,
            "pdf_sha256": digest,
            "pdf_page_url": page_url(d["source_url"], pdf_page),
            "pixels": pixels,
            "page_warnings": warning_summary(json.loads(p[0] or "[]") if p else []),
            "page_warnings_available": p is not None,
            "notice": "Image rendered from the hash-verified original PDF. Physical page numbers include covers.",
        }, image
