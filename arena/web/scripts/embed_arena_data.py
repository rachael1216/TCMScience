#!/usr/bin/env python3
"""Mirror data/*.example.json into assets/fallback-data.js.

Why this exists
---------------
`fetch()` of a `file://` URL is blocked as a cross-origin request by every
current browser, so a double-clicked `index.html` cannot read the JSON that
drives the site. A classic `<script>` tag *is* allowed from `file://`, so the
example documents are mirrored into a JavaScript file that `app.js` consults
after both fetches have failed.

On a server — GitHub Pages, `python3 -m http.server`, anything — the mirror is
never read.

The mirror is generated, never hand-edited, so it cannot disagree with the JSON
except about its age. Run it after regenerating the example set:

    cd arena/web && python3 scripts/embed_arena_data.py

This script reads and writes files on disk. It is repository tooling. It is not
part of the site, it is not served, and nothing in `assets/app.js` calls it.
"""

from __future__ import annotations

import datetime as _datetime
import json
import sys
from pathlib import Path

NAMES = ("tracks", "leaderboard", "runs", "benchmarks", "skills")

HEADER = """/* TCMScience Arena — embedded fallback data.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The site loads `data/<name>.json` (generated results) and falls back to
 * `data/<name>.example.json` when the real file is absent. Both are plain
 * JSON, which is what the data contract requires and what the server build
 * uses. But `fetch()` of a `file://` URL is blocked as a cross-origin request
 * by every current browser, so a double-clicked `index.html` could not read
 * either file.
 *
 * This file is a byte-for-byte mirror of the five `*.example.json` documents,
 * loaded by a classic <script> tag, which `file://` does allow. `app.js`
 * consults it only after both fetches have failed, so on GitHub Pages or any
 * static host it is dead weight and never read.
 *
 * It is generated from the JSON, never edited by hand, so the two cannot
 * disagree about anything except their age:
 *
 *   python3 scripts/embed_arena_data.py     # writes this file
 *
 * Read-only rule (ADR-0004): this is a copy of published output. Nothing here
 * is computed, and nothing here is written back.
 *
 * Generated: {generated_at}
 */
window.ARENA_FALLBACK = window.ARENA_FALLBACK || {};
"""


def main() -> int:
    web = Path(__file__).resolve().parent.parent
    data_dir = web / "data"
    out_path = web / "assets" / "fallback-data.js"

    docs: dict[str, object] = {}
    for name in NAMES:
        path = data_dir / f"{name}.example.json"
        if not path.exists():
            print(f"missing {path} — run the data generator first", file=sys.stderr)
            return 1
        docs[name] = json.loads(path.read_text(encoding="utf-8"))

    generated_at = _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Not str.format: the header contains literal `{}` in JavaScript.
    chunks = [HEADER.replace("{generated_at}", generated_at)]
    chunks.append("window.ARENA_FALLBACK_META = " + json.dumps({
        "mirrors": [f"data/{name}.example.json" for name in NAMES],
        "generated_at": generated_at,
    }, indent=2) + ";\n")

    for name in NAMES:
        chunks.append(
            f"window.ARENA_FALLBACK[{json.dumps(name)}] = "
            + json.dumps(docs[name], indent=2, ensure_ascii=False)
            + ";\n"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(chunks), encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes) from {len(NAMES)} documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
