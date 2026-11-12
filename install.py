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
from tarfile import TarError, open as open_tar
import tempfile
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DATABASE = "data/db/bcrp.sqlite"
PDF_PATH = re.compile(r"data/pdfs/(?:wp/dt-\d{4}-\d{3}|(?:ri|moneda)/[0-9a-f]{64})\.pdf")
SERVER_NAME = "BCRP Corpus"


def register_claude(config, server):
    if config.is_symlink():
        raise ValueError(f"Claude Desktop configuration is a symlink: {config}")
    original = config.read_bytes() if config.exists() else None
    settings = json.loads(original) if original is not None else {}
    if not isinstance(settings, dict) or not isinstance(settings.get("mcpServers", {}), dict):
        raise ValueError(f"Invalid Claude Desktop configuration: {config}")
    servers = settings.setdefault("mcpServers", {})
    if servers.get(SERVER_NAME) == server:
        return
    servers[SERVER_NAME] = server
    if original is not None:
        with tempfile.NamedTemporaryFile(dir=config.parent, prefix=config.name + ".bak-", delete=False) as backup:
            backup.write(original)
    with tempfile.NamedTemporaryFile(dir=config.parent, delete=False, mode="w", encoding="utf-8") as output:
        replacement = Path(output.name)
        try:
            json.dump(settings, output, indent=2)
            output.write("\n")
            output.close()
            replacement.replace(config)
        finally:
            replacement.unlink(missing_ok=True)


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


def extract_tarball(archive, root, validate):
    with open_tar(archive, "r:gz") as bundle, tempfile.TemporaryDirectory(dir=root.parent) as temp:
        staging = Path(temp) / "corpus"
        for entry in bundle:
            parts = entry.name.split("/")
            for index, part in enumerate(parts):
                if part not in ("db", "pdfs"):
                    continue
                name = "data/" + "/".join(parts[index:])
                if name != DATABASE and not PDF_PATH.fullmatch(name):
                    continue
                if not entry.isfile():
                    raise ValueError(f"Expected a regular file in the archive: {entry.name!r}.")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(entry) as member, target.open("xb") as output:
                    shutil.copyfileobj(member, output)
                break
        validate(staging)
        staging.rename(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    runtime = Path(__file__).resolve().parent
    parser.add_argument(
        "corpus",
        type=Path,
        nargs="?",
        help="Corpus archive or extracted directory (default: find it next to the software)",
    )
    parser.add_argument("--print-config", action="store_true", help="Print MCP settings for another client")
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
        if args.corpus and root.name.endswith((".zip", ".tar.gz")) and not root.is_dir():
            archive = root
            root = (
                archive.with_suffix("") if archive.name.endswith(".zip")
                else archive.with_name(archive.name.removesuffix(".tar.gz") + "-data")
            )
        elif not args.corpus and not root.exists():
            archives = sorted(runtime.parent.glob("bcrp-corpus-data-*.zip"))
            tarball = runtime.parent / "bcrp-corpus.tar.gz"
            if tarball.is_file():
                archives.append(tarball)
            if len(archives) != 1:
                raise ValueError(
                    "Place the BCRP Corpus data archive next to the software directory, "
                    "or pass its path to setup."
                )
            archive = archives[0]
        if archive and not root.exists():
            if archive.name.endswith(".tar.gz"):
                extract_tarball(archive, root, validate)
            else:
                extract_archive(archive, root, validate)
        else:
            validate(root)
    except (RetrievalError, sqlite3.Error, OSError, ValueError, BadZipFile, TarError, EOFError) as error:
        parser.error(str(error))
    server = runtime / "serve.py"
    server_args = [str(server), "--root", str(root)]
    if renderer:
        server_args.extend(("--pdftoppm", str(Path(renderer).resolve())))
    server_config = {
        "command": os.path.abspath(sys.executable),
        "args": server_args,
    }
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "Claude"
    if sys.platform.startswith("linux") and config_dir.is_dir() and not args.print_config:
        try:
            register_claude(config_dir / "claude_desktop_config.json", server_config)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        print("BCRP Corpus is configured. Fully quit and reopen Claude Desktop.")
    else:
        print(json.dumps({"mcpServers": {SERVER_NAME: server_config}}, indent=2))


if __name__ == "__main__":
    main()
