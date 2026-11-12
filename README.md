# BCRP Corpus

BCRP Corpus is a local MCP server for searching publications from the Banco
Central de Reserva del Perú. It supports keyword and phrase search across
working papers, Revista Moneda, and Inflation Reports and their boxes. Results
can include passages and physical PDF page numbers.

## Requirements

- Python 3.10 or later with SQLite FTS5 and IANA time zone data.
- An MCP client that can launch a local server over standard input and output.
- A separately supplied, versioned corpus archive and its SHA-256 checksum.
- Poppler's `pdftoppm` on your PATH if you want to view PDF pages as images.

## Install

1. Download and unpack the public source release. A GitHub account is optional.
2. Verify the corpus archive against the SHA-256 checksum supplied with it.
   Extract it into a directory named `bcrp-corpus-data` next to the software
   directory. Preserve `data/db/bcrp.sqlite` and `data/pdfs/` inside it.
3. In the source directory, run:

   ```sh
   python3 install.py
   ```

4. Add the printed server entry to your MCP client's local server settings and
   restart or reload the client. For clients with separate command and argument
   fields, use the values printed under `command` and `args`.

The installer checks Python, time zone data, SQLite FTS5, the corpus database,
and the PDF renderer before printing the configuration. You can also pass
the path to a different extracted corpus directory. The server
reads the archive locally. Keep the runtime release and archive version with
your notes so you can reproduce results. To use a new archive, extract it
separately and rerun the installer with its directory.
