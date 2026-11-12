import re
import unicodedata


def fold(s):
    return "".join(
        c for c in unicodedata.normalize("NFD", s.casefold()) if unicodedata.category(c) != "Mn"
    )


def query_expression(query, advanced=False):
    if not query.strip():
        raise ValueError("Empty query")
    if advanced:
        return query
    if query.count('"') % 2:
        raise ValueError("Unclosed phrase quote")
    parts = re.findall(r'"([^"]+)"|(\S+)', query)
    return " AND ".join('"' + (a or b).replace('"', '""') + '"' for a, b in parts)
