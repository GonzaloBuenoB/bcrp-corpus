"""Resolve an inflation report using recorded release and artifact evidence."""

from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

REPORT_ID_PATTERN = r"ri-\d{4}-(?:0[1-9]|1[0-2])-(?:es|en)"


def validate_report_id(value):
    if not isinstance(value, str) or not re.fullmatch(REPORT_ID_PATTERN, value):
        raise ValueError("Use a report ID such as ri-2026-09-es.")
    return value


def parse_date(value):
    if not isinstance(value, str):
        raise ValueError("Use an as-of date in YYYY-MM-DD form.")
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Use an as-of date in YYYY-MM-DD form.") from exc
    if day.isoformat() != value:
        raise ValueError("Use an as-of date in YYYY-MM-DD form.")
    return day


def acquired_day(value):
    if not value:
        return None
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        return None
    return stamp.astimezone(ZoneInfo("America/Lima")).date()


def resolve(c, report_id, language, as_of):
    """Return a selection or a specific abstention. Cutoffs are inclusive Lima dates."""
    cutoff = parse_date(as_of) if as_of is not None else None
    if report_id is not None:
        validate_report_id(report_id)
    if language not in (None, "es", "en"):
        raise ValueError("Report language must be es or en.")
    selected_language = language or (report_id.rsplit("-", 1)[1] if report_id else "es")
    if report_id and not report_id.endswith("-" + selected_language):
        raise ValueError("Report ID and language disagree.")
    result = {"status": "unavailable", "language": selected_language, "as_of": as_of,
              "requested_report_id": report_id, "report": None}
    if not c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ri_entities'"
    ).fetchone():
        return result | {"reason": "inflation_report_registry_unavailable"}
    rows = [dict(row) for row in c.execute(
        """SELECT d.id AS document_id,d.title,d.source_url,d.language,d.state,
                  e.edition AS edition_id,r.value AS publication_date,
                  r.precision AS publication_precision,r.status AS publication_status,
                  r.earliest AS release_earliest,r.latest AS release_latest,
                  a.sha256 AS artifact_sha256,a.acquired_at AS artifact_acquired_at,
                  a.available_at_status AS artifact_availability_status,
                  a.page_count AS physical_page_count
           FROM ri_entities e JOIN documents d ON d.id=e.doc
           LEFT JOIN ri_date_assertions r ON r.doc=e.doc AND r.date_kind='publication' AND r.is_selected=1
           LEFT JOIN ri_occurrences o ON o.doc=e.doc AND o.role='canonical' AND o.physical_page=1
           LEFT JOIN ri_artifacts a ON a.id=o.artifact
           WHERE e.kind='report' AND d.language=? AND (? IS NULL OR d.id=?)
           ORDER BY e.edition DESC""",
        (selected_language, report_id, report_id),
    )]
    inventory_observed = c.execute("SELECT max(observed_at) FROM ri_source_observations").fetchone()[0]
    result["inventory_observed_at"] = inventory_observed
    if not rows:
        return result | {"reason": "report_not_in_local_corpus"}
    if cutoff is not None:
        known = [row for row in rows if row["release_latest"] and parse_date(row["release_latest"]) <= cutoff]
        if not known:
            return result | {"reason": "no_confirmed_release_by_cutoff"}
        # Prefer the latest edition among documented releases. Keep unverified vintage
        # visible rather than substituting an older report or silently citing today's PDF.
        candidate = known[0]
        day = acquired_day(candidate["artifact_acquired_at"])
        if day is None or day > cutoff:
            return result | {"reason": "artifact_vintage_unverified_at_cutoff",
                             "candidate_report_id": candidate["document_id"],
                             "candidate_publication_date": candidate["publication_date"]}
    else:
        candidate = rows[0]
        if report_id is None and not candidate["release_latest"]:
            return result | {"reason": "latest_edition_release_unverified",
                             "candidate_report_id": candidate["document_id"]}
    if candidate["state"] != "indexed" or not candidate["artifact_sha256"]:
        return result | {"reason": "report_text_or_artifact_unavailable",
                         "candidate_report_id": candidate["document_id"]}
    coverage = dict(c.execute(
        "SELECT d.state,count(*) FROM ri_entities e JOIN documents d ON d.id=e.doc "
        "WHERE e.parent_doc=? GROUP BY d.state", (candidate["document_id"],)
    ))
    return result | {
        "status": "resolved", "report": candidate,
        "scope": "indexed local inventory",
        "recuadro_coverage": {"indexed": coverage.get("indexed", 0),
                             "metadata_only": coverage.get("metadata_only", 0)},
        "as_of_basis": "selected release date and verified local acquisition by cutoff" if cutoff else None,
        "unknown_release_count_in_scope": sum(not row["release_latest"] for row in rows),
    }
