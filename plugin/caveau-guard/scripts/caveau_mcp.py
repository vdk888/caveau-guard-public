#!/usr/bin/env python3
"""Caveau MCP server — anonymised file reading for Cowork ("PII from anywhere").

WHY THIS EXISTS (the Cowork workaround)
---------------------------------------
Cowork RUNS our PostToolUse hook but IGNORES `updatedToolOutput` for built-in
tools like Read/Bash (anthropics/claude-code#32105 — output rewrite only takes
effect for MCP tools). So the ambient "anonymise whatever the agent reads" tier
can't work by rewriting a built-in Read in Cowork.

The fix: make the agent read client data THROUGH this MCP tool instead. An MCP
tool's OWN returned content is what lands in context — so if `caveau_read`
returns already-anonymised text, the agent only ever sees `⟦…⟧` tokens. No
rewrite needed; we control the output at the source. The folder guard
(PreToolUse) still blocks the bare `Read` of protected files, which is what
steers the agent to `caveau_read`.

DESIGN
------
- Pure-stdlib stdio JSON-RPC (MCP). No `mcp` pip package → stays zero-install,
  consistent with the rest of the plugin. Reads requests as line-delimited JSON
  on stdin, writes responses on stdout.
- Reuses the vendored engine + extractor + policy + the warm NER daemon (same
  detection the PostToolUse hook uses), and the same session vault (so tokens
  are consistent across the folder path and this path; reversible locally).
- Fail-safe: if anonymisation can't run, it returns an ERROR, never the raw
  text. (Unlike the ambient hook which fails open — here, returning raw PII
  would defeat the tool's whole purpose, so it fails CLOSED.)

Exposes one tool: caveau_read(path) -> anonymised text of the file.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(os.environ.get(
    "CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parent.parent))
_HERE = Path(__file__).resolve().parent
CAVEAU_HOME = Path(os.environ.get("CAVEAU_HOME", Path.home() / ".caveau"))
VAULT_DIR = CAVEAU_HOME / "vaults"

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "caveau", "version": "1.0.0"}

TOOLS = [{
    "name": "caveau_read",
    "description": (
        "Read a client file and return it ANONYMISED — names, IBANs, emails and "
        "other identifying data are replaced with reversible ⟦…⟧ tokens before "
        "you see them. Use this INSTEAD of the plain Read tool for any file that "
        "may contain client PII (the caveau guard blocks the raw Read of protected "
        "folders). Handles .pdf, .docx, .txt, .md, .csv, .json. The real values "
        "never enter your context; they stay in a local vault and are restored "
        "when the final answer is handed back to the user."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "Absolute path to the file to read anonymised."}
        },
        "required": ["path"],
    },
}]


def _vendor():
    for cand in (PLUGIN_ROOT / "vendor", _HERE / "vendor", _HERE.parent / "vendor"):
        if (cand / "caveau").is_dir():
            return cand
    return PLUGIN_ROOT / "vendor"


def _scripts_dir():
    for cand in (PLUGIN_ROOT / "scripts", _HERE):
        if (cand / "caveau_extract.py").is_file():
            return cand
    return _HERE


def _anonymise_file(path: str) -> str:
    """Extract + anonymise a file. Raises on failure (fail-closed)."""
    sys.path.insert(0, str(_vendor()))
    sys.path.insert(0, str(_scripts_dir()))
    from caveau_extract import extract_file          # PDF/docx/text → text
    from caveau import AnonymizationEngine, Vault
    from caveau import policy as _policy

    p = Path(os.path.expanduser(path)).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"no such file: {p}")
    text = extract_file(p)                            # fail-closed on scanned PDFs

    # daemon NER detector if the warm daemon is up (reuse the hook's logic)
    sys.path.insert(0, str(_scripts_dir()))
    detectors = []
    try:
        import posttool_anonymize as _pt
        d = _pt._daemon_detector(text)                # None if daemon down → regex only
        if d:
            detectors.append(d)
    except Exception:
        pass

    pol = _policy.load_policy()
    engine = AnonymizationEngine(
        extra_detectors=detectors,
        match_filter=_policy.make_match_filter(pol))

    # consistent, reversible vault per session
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    mission = os.environ.get("CAVEAU_SESSION", "mcp-session")
    vpath = VAULT_DIR / f"{mission}.vault.json"
    if vpath.is_file():
        engine.vault = Vault.load(str(vpath))
    else:
        engine.vault = Vault(mission=mission)

    res = engine.anonymize(text)
    engine.vault.save(str(vpath))

    note = "" if res.safe_to_send else (
        "\n\n[⚠️ Caveau : une relecture humaine est conseillée — "
        "une donnée potentiellement sensible est restée sous le seuil de confiance.]")
    return res.anonymized + note


# ---- minimal JSON-RPC / MCP plumbing (stdio) -------------------------------

def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _result(id_, result) -> None:
    _send({"jsonrpc": "2.0", "id": id_, "result": result})


def _error(id_, code, message) -> None:
    _send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def _handle(req: dict) -> None:
    method = req.get("method")
    id_ = req.get("id")
    params = req.get("params", {}) or {}

    if method == "initialize":
        _result(id_, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    elif method == "notifications/initialized":
        pass  # notification, no response
    elif method == "tools/list":
        _result(id_, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        if name != "caveau_read":
            _error(id_, -32601, f"unknown tool: {name}")
            return
        try:
            text = _anonymise_file(args.get("path", ""))
            _result(id_, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            # fail-CLOSED: return an error, never the raw file
            _result(id_, {
                "content": [{"type": "text",
                             "text": f"⛔ Caveau n'a pas pu anonymiser ce fichier : {e}. "
                                     "Le contenu brut n'est PAS renvoyé (sécurité)."}],
                "isError": True,
            })
    elif id_ is not None:
        _error(id_, -32601, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        try:
            _handle(req)
        except Exception as e:
            if isinstance(req, dict) and req.get("id") is not None:
                _error(req.get("id"), -32603, f"internal error: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
