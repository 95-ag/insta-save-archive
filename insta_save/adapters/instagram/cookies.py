"""JSON session cookies → Netscape format (yt-dlp's --cookies requires Netscape).

NOTE: engines/transcript.py carries its own converter; consolidating the two is a
later cleanup (out of scope for ingest)."""

import json
from pathlib import Path


def json_cookies_to_netscape(json_path, out_path) -> str:
    src = json.loads(Path(json_path).read_text(encoding="utf-8"))
    lines = ["# Netscape HTTP Cookie File"]
    for c in src:
        domain = c.get("domain", ".instagram.com")
        expires = c.get("expires", 0)
        expires = 0 if expires in (-1, None) else int(expires)
        lines.append("\t".join([
            domain, "TRUE" if domain.startswith(".") else "FALSE",
            c.get("path", "/"), "TRUE" if c.get("secure") else "FALSE",
            str(expires), c["name"], c["value"],
        ]))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)
