"""Check a local BCRP Corpus installation and print its MCP server configuration."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    runtime = Path(__file__).resolve().parent
    parser.add_argument(
        "corpus_root",
        type=Path,
        nargs="?",
        default=runtime.parent / "bcrp-corpus-data",
        help="Extracted corpus directory (default: bcrp-corpus-data next to the software)",
    )
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("Install Python 3.10 or later and rerun setup.")
    try:
        ZoneInfo("America/Lima")
    except ZoneInfoNotFoundError:
        parser.error("Install IANA time zone data and rerun setup.")
    try:
        with sqlite3.connect(":memory:") as probe:
            probe.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
    except sqlite3.Error as error:
        parser.error(f"Python needs SQLite with FTS5 support: {error}")

    from retrieval.wp import Corpus, RetrievalError

    renderer = shutil.which("pdftoppm")
    if not renderer:
        print("Install Poppler and put pdftoppm on PATH to enable PDF page images.", file=sys.stderr)
    root = args.corpus_root.resolve()
    if not (root / "data/db/bcrp.sqlite").is_file() or not (root / "data/pdfs").is_dir():
        parser.error(f"Extract the BCRP Corpus data archive into {root} and rerun setup.")
    corpus = Corpus(root)
    try:
        with corpus.connection() as database:
            database.execute("SELECT rowid FROM search_index LIMIT 1")
    except (RetrievalError, sqlite3.Error) as error:
        parser.error(str(error))
    server = runtime / "serve.py"
    server_args = [str(server), "--root", str(root)]
    if renderer:
        server_args.extend(("--pdftoppm", str(Path(renderer).resolve())))
    print(json.dumps({"mcpServers": {"BCRP Corpus": {
        "command": os.path.abspath(sys.executable),
        "args": server_args,
    }}}, indent=2))


if __name__ == "__main__":
    main()
