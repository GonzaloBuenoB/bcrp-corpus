"""Check a local BCRP Corpus installation and print its MCP server configuration."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

from retrieval.wp import Corpus, RetrievalError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus_root", type=Path, help="Extracted versioned corpus directory")
    args = parser.parse_args()
    root = args.corpus_root.resolve()
    corpus = Corpus(root)
    try:
        with sqlite3.connect(":memory:") as probe:
            probe.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        with corpus.connection() as database:
            database.execute("SELECT rowid FROM search_index LIMIT 1")
    except (RetrievalError, sqlite3.Error) as error:
        parser.error(str(error))
    if not (root / "data/pdfs").is_dir():
        parser.error("Expected the archive's data/pdfs directory.")
    if not shutil.which("pdftoppm"):
        print("Install Poppler and put pdftoppm on PATH to enable PDF page images.", file=sys.stderr)

    server = Path(__file__).resolve().parent / "serve.py"
    print(json.dumps({"mcpServers": {"BCRP Corpus": {
        "command": os.path.abspath(sys.executable),
        "args": [str(server), "--root", str(root)],
    }}}, indent=2))


if __name__ == "__main__":
    main()
