"""Check a local BCRP Corpus installation and print its MCP server configuration."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DATABASE = "data/db/bcrp.sqlite"
PDF_PATH = re.compile(r"data/pdfs/(?:wp/dt-\d{4}-\d{3}|(?:ri|moneda)/[0-9a-f]{64})\.pdf")


def extract_archive(archive, root, validate):
    checksum = archive.with_name(archive.name + ".sha256")
    if not checksum.is_file():
        raise ValueError(f"Place {checksum.name} next to the archive and rerun setup.")
    fields = checksum.read_text(encoding="utf-8").split(maxsplit=1)
    if not fields or not re.fullmatch(r"[a-fA-F0-9]{64}", fields[0]):
        raise ValueError(f"Invalid checksum in {checksum.name}.")
    with archive.open("rb") as source:
        digest = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
        if digest.hexdigest() != fields[0].lower():
            raise ValueError(f"Checksum mismatch for {archive.name}. Get a fresh copy and rerun setup.")
        source.seek(0)
        with ZipFile(source) as bundle, tempfile.TemporaryDirectory(dir=root.parent) as temp:
            staging = Path(temp) / "corpus"
            for entry in bundle.infolist():
                if entry.is_dir():
                    continue
                name = entry.filename
                if name != DATABASE and not PDF_PATH.fullmatch(name):
                    raise ValueError(f"Unexpected archive entry: {name!r}.")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as member, target.open("xb") as output:
                    shutil.copyfileobj(member, output)
            validate(staging)
            staging.rename(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    runtime = Path(__file__).resolve().parent
    parser.add_argument(
        "corpus",
        type=Path,
        nargs="?",
        help="Corpus ZIP or extracted corpus directory (default: find it next to the software)",
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
    def validate(root):
        if not (root / DATABASE).is_file() or not (root / "data/pdfs").is_dir():
            raise ValueError(f"Expected the SQLite index and PDF directory in {root}.")
        with Corpus(root).connection() as database:
            database.execute("SELECT rowid FROM search_index LIMIT 1")

    try:
        root = args.corpus.resolve() if args.corpus else runtime.parent / "bcrp-corpus-data"
        archive = None
        if args.corpus and root.suffix.lower() == ".zip" and not root.is_dir():
            archive = root
            root = root.with_suffix("")
        elif not args.corpus and not root.exists():
            archives = sorted(runtime.parent.glob("bcrp-corpus-data-*.zip"))
            if len(archives) != 1:
                raise ValueError(
                    "Place one BCRP Corpus data ZIP and its .sha256 file next to the software "
                    "directory, or pass the path to the ZIP."
                )
            archive = archives[0]
        if archive and not root.exists():
            extract_archive(archive, root, validate)
        else:
            validate(root)
    except (RetrievalError, sqlite3.Error, OSError, ValueError, BadZipFile) as error:
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
