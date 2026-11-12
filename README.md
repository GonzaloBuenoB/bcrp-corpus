# BCRP Corpus

BCRP Corpus is a local MCP server for searching publications from the Banco
Central de Reserva del Perú. It supports keyword and phrase search across
working papers, Revista Moneda, and Inflation Reports and their boxes. Results
can include passages and physical PDF page numbers.

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
