"""Document filters and keyword ranking."""

import json

from corpus.common import bounds
from retrieval.ri import validate_report_id
from retrieval.query import fold, query_expression

PASSAGE_ELIGIBLE = "d.state='indexed' AND k.doc=d.id AND p.doc=k.doc AND has_alnum(k.text) AND in_pdf_range(d.metadata_json,k.page)"


def in_range(metadata, page):
    try:
        lo, hi = bounds(json.loads(metadata or "{}"))
        return int(lo <= page <= hi)
    except (ValueError, TypeError, KeyError):
        return 0


def configure(c):
    c.create_function("in_pdf_range", 2, in_range)
    c.create_function(
        "has_alnum", 1, lambda text: int(any(ch.isalnum() for ch in (text or "")))
    )


def document_filters(
    *,
    year_from=None,
    year_to=None,
    author=None,
    language=None,
    jel_status=None,
    jel=None,
    include_candidates=False,
    collection=None,
    issue=None,
    report_id=None,
):
    if (
        jel_status in ("unverified_candidate", "candidate_found_needs_review")
        and not include_candidates
    ):
        raise ValueError("Candidate filters require explicit include_candidates")
    if collection not in (None, "all", "working_paper", "moneda", "inflation_report", "inflation_box"):
        raise ValueError("Invalid collection")
    if issue is not None and (type(issue) is not int or not 1 <= issue <= 9999):
        raise ValueError("Invalid issue")
    clauses, args = [], []
    for col, op, value in [
        ("year", ">=", year_from),
        ("year", "<=", year_to),
        ("language", "=", language),
    ]:
        if value is not None:
            clauses.append(f"d.{col} {op} ?")
            args.append(value)
    if collection not in (None, "all"):
        clauses.append("d.collection=?")
        args.append(collection)
    if report_id is not None:
        validate_report_id(report_id)
        clauses.append(
            "(d.id=? OR (d.collection='inflation_box' AND json_extract(d.metadata_json,'$.parent_id')=?))"
        )
        args.extend((report_id, report_id))
    if issue is not None:
        clauses.append(
            "d.collection='moneda' AND json_extract(d.metadata_json,'$.issue_number')=?"
        )
        args.append(issue)
    if author:
        clauses.append(
            "EXISTS(SELECT 1 FROM authors a WHERE a.doc=d.id AND instr(a.normalized,?)>0)"
        )
        args.append(fold(author))
    if jel_status:
        clauses.append("d.jel_status=?")
        args.append(jel_status)
    if jel:
        clauses.append(
            "EXISTS(SELECT 1 FROM jel j WHERE j.doc=d.id AND j.code=? "
            + ("" if include_candidates else "AND j.status='portal_sourced'")
            + ")"
        )
        args.append(jel.upper())
    return clauses, args


def lexical(c, query, filters, kind, limit):
    configure(c)
    clauses, args = document_filters(**filters)
    clauses.insert(0, "search_index MATCH ?")
    args.insert(0, query_expression(query))
    clauses.append("(search_index.chunk_id IS NULL OR (" + PASSAGE_ELIGIBLE + "))")
    if kind != "all":
        clauses.append(
            "search_index.chunk_id IS " + ("NOT NULL" if kind == "passage" else "NULL")
        )
    sql = (
        """SELECT d.id,k.page AS pdf_page,k.ordinal,k.section,k.text AS passage,
    p.warnings_json AS page_warnings_json,
    snippet(search_index,-1,'[',']',' … ',36) AS snippet,bm25(search_index,5,2,1) AS rank
    FROM search_index JOIN documents d ON d.id=search_index.doc
    LEFT JOIN chunks k ON k.id=search_index.chunk_id
    LEFT JOIN pages p ON p.doc=k.doc AND p.page=k.page WHERE """
        + " AND ".join(clauses)
        + " ORDER BY rank,d.id,k.page,k.ordinal"
    )
    # Passage-only rankings are unchanged. Metadata duplicates must be removed
    # before the unique-hit quota, otherwise they consume limit/has_more slots.
    if kind == "passage":
        sql += " LIMIT ?"
        args = [*args, limit]
    result = []
    metadata_seen = set()
    cursor = c.execute(sql, args)
    try:
        for row in cursor:
            item = dict(row)
            item["match_type"] = "metadata" if row["pdf_page"] is None else "passage"
            if item["match_type"] == "metadata":
                key = item["id"], item["snippet"]
                if key in metadata_seen:
                    continue
                metadata_seen.add(key)
            item["page_warnings"] = json.loads(item.pop("page_warnings_json") or "{}")
            result.append(item)
            if len(result) == limit:
                break
    finally:
        cursor.close()
    return result
