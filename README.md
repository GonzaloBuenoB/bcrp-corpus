# BCRP Corpus

BCRP Corpus is a local MCP server for searching publications from the Banco
Central de Reserva del Perú. It supports keyword and phrase search across
working papers, Revista Moneda, and Inflation Reports and their boxes. Results
can include passages and physical PDF page numbers.

## Current release and planned profiles

The currently available installer is **keyword-only**. A Full profile with semantic and hybrid retrieval and optional neural reranking is planned but not yet available. The planned Full profile will be the default for research trials; Lite will remain an optional keyword-only profile. Both will retain original-page evidence, scope, filters and provenance, and use separately supplied compatible corpus snapshots. Full will require pinned inference dependencies and a matching precomputed semantic index, not local corpus rebuilding. Acquisition, conversion and automatic corpus maintenance are outside this portable product.

The commands below describe the current keyword-only release. Do not interpret a successful installation as acceptance of the planned Full profile.

## Requirements

- Python 3.10 or later with SQLite FTS5 and IANA time zone data.
- An MCP client that can launch a local server over standard input and output.
- A separately supplied corpus data archive.
- Poppler's `pdftoppm` on your PATH if you want to view PDF pages as images.

## Install

1. Download and unpack the public source release. A GitHub account is optional.
2. Place the supplied `bcrp-corpus.tar.gz` next to the software directory.
   If you received a `bcrp-corpus-data-*.zip`, place it and its supplied
   `.zip.sha256` file there.
3. In the source directory, run:

   ```sh
   python3 install.py
   ```

4. If setup finds Claude Desktop's standard Linux configuration directory,
   it registers BCRP Corpus automatically. Fully quit and reopen Claude Desktop.
   For other MCP clients, add the server entry printed by setup and reload the client.
   Use `--print-config` to print the entry on any platform.

Setup extracts the data locally, verifies the supplied checksum for ZIP archives, and checks the runtime
before configuring the connection. To install a different snapshot, run
`python3 install.py` with the path to its archive.
You can also pass the path to an already extracted corpus directory.


## Optional broad research workflow

The experimental wrapper uses the local keyword index to find candidate
documents, then looks inside shortlisted research papers for matching body
pages. It supplies the selected text, physical PDF page references and quality
warnings to Claude Code. It checks that requested sections have content and
allows one completion pass for missing sections.

Run it from the software directory with a signed-in Claude Code installation:

```sh
python3 -B research.py question.md plan.json new-output-directory --root /path/to/extracted/corpus
```

The JSON plan contains a `needs` list with `label`, `collection`, `query`,
and `fallback` for each evidence need. An optional `passage_query` can
specify body-search terms in the source language. Set `deliverables` to the
exact Markdown section titles requested. The output directory stores the
answer, local evidence packet, and execution traces. Use the direct MCP
workflow for factual lookups.

Retrieval runs locally. Claude Code receives the question and retrieved
excerpts when drafting. The basic evidence handoff has been checked.
Full-answer coverage, citation accuracy, and consistent research quality
still need evaluation.
