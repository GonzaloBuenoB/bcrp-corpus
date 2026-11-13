"""Opt-in broad research workflow for the local corpus and Claude Code."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from retrieval.ranking import lexical
from retrieval.wp import Corpus, RetrievalError

ROOT = Path(__file__).resolve().parent
COLLECTIONS = {"working_paper", "moneda", "inflation_report", "inflation_box"}
MAX_NEEDS = 6
MAX_DELIVERABLES = 6


def load_plan(path):
    plan = json.loads(path.read_text())
    needs, deliverables = plan["needs"], plan["deliverables"]
    if not isinstance(needs, list) or not 1 <= len(needs) <= MAX_NEEDS:
        raise ValueError("Provide one to six evidence needs.")
    if not isinstance(deliverables, list) or not 1 <= len(deliverables) <= MAX_DELIVERABLES:
        raise ValueError("Provide one to six deliverable headings.")
    if any(not isinstance(s, str) or not s.strip() or "\n" in s for s in deliverables):
        raise ValueError("Deliverable headings must be nonempty single lines.")
    if len(set(deliverables)) != len(deliverables):
        raise ValueError("Deliverable headings must be distinct.")
    for need in needs:
        if need["collection"] not in COLLECTIONS:
            raise ValueError("Use a supported collection for each need.")
        for key in ("label", "query", "fallback"):
            if not isinstance(need.get(key), str) or not need[key].strip() or len(need[key]) > 500:
                raise ValueError(f"Invalid evidence need field: {key}")
        if "passage_query" in need:
            value = need["passage_query"]
            if not isinstance(value, str) or not value.strip() or len(value) > 500:
                raise ValueError("Invalid evidence need field: passage_query")
        if need["fallback"] == need["query"]:
            raise ValueError("Supply a distinct fallback query.")
    return plan


def research_pages(corpus, need, leads, row):
    """Look for matching body passages in shortlisted papers before drafting."""
    query = need.get("passage_query", row["searches"][-1]["query"])
    row["passage_searches"] = []
    for lead in leads:
        ident = lead["document_id"]
        with corpus.connection() as connection:
            hits = lexical(connection, query, {}, "passage", 24, document_id=ident)
        row["passage_searches"].append({"document_id": ident, "query": query,
                                        "hits": len(hits)})
        # A heading on the physical page is stronger evidence of section type
        # than a chunk's inherited heading, which can span several pages.
        pages = {}
        for hit in hits:
            page = hit["pdf_page"]
            if page > 3 and page not in pages:
                pages[page] = corpus.passage(ident, page, max_characters=2600)
                if len(pages) == 8:
                    break
        substantive = [page for page in pages.values() if not page["empty_page"]]
        if substantive:
            def section_priority(page):
                heading = re.match(
                    r"\s*(?:```[^\n]*\n)?[ #]*[\d. ]*"
                    r"(?:results|resultados|conclusions?|concluding remarks|conclusiones)\b",
                    page["text"], re.IGNORECASE,
                )
                return heading is None

            return sorted(substantive, key=section_priority)[:2]
    return []


def discover(corpus, needs):
    ledger = []
    for need in needs:
        collection = need["collection"]
        kind = "metadata" if collection in {"working_paper", "moneda"} else "passage"
        attempts = []
        row = {"label": need["label"], "collection": collection,
               "searches": attempts, "leads": [], "pages": [], "status": "open"}
        ledger.append(row)
        try:
            for query in (need["query"], need["fallback"]):
                result = corpus.search(query, collection=collection, kind=kind, limit=5)
                hits = result["metadata_matches"] if kind == "metadata" else result["passages"]
                attempts.append({"query": query, "hits": len(hits)})
                if hits:
                    break
            unique = {}
            for hit in hits:
                unique.setdefault(hit["document_id"], hit)
            leads = list(unique.values())[:3]
            row["leads"] = [{k: hit[k] for k in ("document_id", "title")} for hit in leads]
            if leads:
                lead = leads[0]
                if kind == "metadata":
                    pages = research_pages(corpus, need, leads, row)
                else:
                    pages = [corpus.passage(lead["document_id"], lead["pdf_page"],
                                            max_characters=2300)]
                fields = ("document_id", "title", "pdf_page", "text", "next_offset",
                          "quality_status", "warnings", "page_warnings", "source_url",
                          "pdf_page_url")
                row["pages"] = [{k: page[k] for k in fields if k in page}
                                for page in pages[:2] if not page["empty_page"]]
                if row["pages"]:
                    row["status"] = "candidate evidence"
        except RetrievalError as exc:
            row["error"] = {"code": exc.code, "message": str(exc)}
    return ledger


def missing_deliverables(answer, headings):
    sections = re.split(r"(?m)^##[ \t]+", answer)
    present = set()
    for section in sections[1:]:
        title, _, body = section.partition("\n")
        if body.strip():
            present.add(title.strip())
    return [heading for heading in headings if heading not in present]


def run_claude(prompt, trace, *, corpus_root=None):
    mcp = corpus_root is not None
    cmd = ["claude", "-p", "--model", "haiku", "--effort", "low",
           "--max-turns", "12" if mcp else "1", "--output-format", "stream-json",
           "--verbose", "--no-session-persistence", "--strict-mcp-config",
           "--setting-sources", "", "--tools", ""]
    if mcp:
        server = {"command": sys.executable,
                  "args": ["-B", "-m", "mcp_server.wp", "--root", str(corpus_root)]}
        cmd.extend(["--mcp-config", json.dumps({"mcpServers": {"bcrp-working-papers": server}}),
                    "--allowedTools", "mcp__bcrp-working-papers__*"])
    with trace.open("x") as output:
        subprocess.run(cmd, input=prompt, text=True, stdout=output, cwd=ROOT,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                       check=True, timeout=210 if mcp else 65)
    with trace.open() as stream:
        result = next((event for event in map(json.loads, stream)
                       if event["type"] == "result"), None)
    if not result or result.get("is_error") or not result.get("result"):
        raise RuntimeError(f"Claude did not return a draft. See {trace}.")
    return result["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", type=Path)
    parser.add_argument("plan", type=Path, help="Explicit evidence needs and requested headings")
    parser.add_argument("output", type=Path, help="New directory for the answer and audit records")
    parser.add_argument("--root", type=Path, default=ROOT, help="Extracted corpus root")
    args = parser.parse_args()
    corpus_root = args.root.resolve()
    plan = load_plan(args.plan)
    question = args.question.read_text()
    args.output.mkdir()
    corpus = Corpus(corpus_root)
    ledger = discover(corpus, plan["needs"])
    packet = json.dumps(ledger, ensure_ascii=False, indent=2)
    (args.output / "evidence.json").write_text(packet + "\n")
    headings = plan["deliverables"]
    outline = "\n".join(f"## {h}" for h in headings)
    prompt = f"""Answer the research request below using only the BCRP corpus.
Return every requested deliverable in this reply, under these exact Markdown headings:
{outline}
Use the local evidence packet first. A lead or candidate page only supports a claim if its text
directly addresses it. Check results and conclusions for the requested outcomes before
inferring a gap from an abstract. Distinguish official assessments, observed outcomes, projections and
individual research. Cite physical pages and preserve relevant warnings. Use narrowly scoped
MCP calls for material gaps. Reserve enough space for all deliverables and report open evidence
gaps within the requested sections.

Request:
{question}

Local evidence packet:
{packet}
"""
    prompt += "\nThe connector supports keyword search only. Omit mode and rerank.\n"
    draft = run_claude(prompt, args.output / "draft.jsonl", corpus_root=corpus_root)
    (args.output / "answer.md").write_text(draft + "\n")
    missing = missing_deliverables(draft, headings)
    if missing:
        required = "\n".join(f"## {h}" for h in missing)
        completion = run_claude(
            f"""Complete only the missing deliverables under these exact headings:
{required}
Use the verified evidence available in the draft and packet. Do not introduce unsupported
claims or citations. Preserve material gaps. Follow the original request's format and language.

Request:
{question}

Draft:
{draft}

Evidence packet:
{packet}
""", args.output / "completion.jsonl")
        draft += "\n\n" + completion
        (args.output / "answer.md").write_text(draft + "\n")
    remaining = missing_deliverables(draft, headings)
    (args.output / "completion-check.json").write_text(json.dumps({
        "initially_missing": missing, "still_missing": remaining,
        "check_scope": "Exact headings with nonempty bodies. Evidence and content require review.",
    }, indent=2) + "\n")
    if remaining:
        parser.exit(2, "Incomplete deliverables: " + ", ".join(remaining) + "\n")
    print(args.output / "answer.md")


if __name__ == "__main__":
    main()
