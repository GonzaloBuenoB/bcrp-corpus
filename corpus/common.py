"""Shared identifiers, safe source paths and verified physical-page bounds."""

import re
from pathlib import Path

ID_PATTERN = r"(?:dt-\d{4}-\d{3}|moneda-\d{1,4}-(?:\d{2,3}|u[0-9a-f]{12})|ri-\d{4}-(?:0[1-9]|1[0-2])-(?:es|en)(?:-box-\d{2,3})?)"


def validate_id(ident, collection=None):
    if not isinstance(ident, str) or not re.fullmatch(ID_PATTERN, ident):
        raise ValueError("Invalid corpus document ID")
    if collection == "moneda" and not ident.startswith("moneda-"):
        raise ValueError("Expected Moneda ID")
    return ident


def safe_path(root, relative):
    root = Path(root).resolve()
    p = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or p.resolve() != p:
        raise ValueError("Unsafe or symlinked corpus path")
    return p


def pdf_relative(meta):
    ident = validate_id(meta["id"])
    if ident.startswith("dt-"):
        return f"data/pdfs/wp/{ident}.pdf"
    digest = meta.get("pdf", {}).get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Missing or invalid registered PDF hash")
    collection_dir = "ri" if ident.startswith("ri-") else "moneda"
    return f"data/pdfs/{collection_dir}/{digest}.pdf"


def bounds(meta):
    pdf = meta.get("pdf", {})
    count = pdf.get("page_count")
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError("Unconfirmed PDF page count")
    ident = meta["id"]
    if ident.startswith("ri-") and "-box-" in ident:
        span = meta.get("physical_page_range") or {}
        lo, hi = span.get("start"), span.get("end")
        if (span.get("status") != "verified" or span.get("pdf_sha256") != pdf.get("sha256")
            or pdf.get("kind") != "report" or not span.get("evidence")
            or type(lo) is not int or type(hi) is not int or not 1 <= lo <= hi <= count):
            raise ValueError("Unverified recuadro physical-page bounds")
        return lo, hi
    if not ident.startswith("moneda-"):
        return 1, count
    span = meta.get("physical_page_range") or {}
    lo, hi = span.get("start"), span.get("end")
    if span.get("status") != "verified" or not span.get("evidence"):
        raise ValueError("Article physical-page range is unverified")
    if type(lo) is not int or type(hi) is not int or not 1 <= lo <= hi <= count:
        raise ValueError("Invalid article physical-page range")
    if pdf.get("kind") not in ("article", "issue"):
        raise ValueError("Unconfirmed PDF role")
    if pdf["kind"] == "issue" and span.get("pdf_sha256") != pdf.get("sha256"):
        raise ValueError("Range verification belongs to another PDF version")
    if pdf["kind"] == "article" and (lo, hi) != (1, count):
        raise ValueError("Standalone article must cover its entire original PDF")
    return lo, hi
