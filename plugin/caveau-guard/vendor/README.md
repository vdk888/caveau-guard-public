# vendor/ — bundled dependencies (do not edit)

This folder makes the plugin **self-contained** so it runs from a GitHub install
or a Cowork zip with **no `pip install` and no network** — the same approach as
Bubble Sentinel.

- `caveau/` — the Caveau anonymisation engine (pure-stdlib core). Synced from the
  Caveau repo; the firm-identity `deployment_allowlist.json` is intentionally NOT
  bundled (only the `.example`).
- `pypdf/` — pure-python PDF text reader (vendored so PDFs work with no install).

`.docx` is read with the Python standard library (zipfile + ElementTree) in
`scripts/caveau_extract.py`, so no `python-docx`/`lxml` is needed either.

Scripts add this dir to `sys.path` via `${CLAUDE_PLUGIN_ROOT}/vendor`.
To refresh after an engine change, re-run the vendor sync (see RELEASING.md).
