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
2. Place the supplied `bcrp-corpus-data-*.zip` and its `.zip.sha256` file
   next to the software directory.
3. In the source directory, run:

   ```sh
   python3 install.py
   ```

4. Add the printed server entry to your MCP client's local server settings and
   restart or reload the client. For clients with separate command and argument
   fields, use the values printed under `command` and `args`.

Setup verifies the checksum, extracts the data locally, and checks the runtime
before printing the configuration. To install a different snapshot, run
`python3 install.py` with the path to its ZIP, then use the printed settings.
You can also pass the path to an already extracted corpus directory.
