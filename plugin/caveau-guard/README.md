# Caveau Guard — fail-closed PII guard for Cowork / Claude Code

A Claude Code **plugin** that stops Claude from reading raw client data. While
enabled, any attempt to `Read`/`Grep`/`Glob`/`Edit`/`Write`/`Bash` a file inside
a **protected client folder** is **denied** — Claude is told to run the data
through [Caveau](../../README.md) first and work on the local, reversible,
anonymised copy. The token↔value vault never leaves the machine.

This is **Jalon 2** of Caveau: the engine (Jalon 1) anonymises; this plugin
*enforces* that nothing identifying reaches the model in clear.

> **Surface:** hooks run wherever the Claude Code engine runs — **Cowork**, the
> Claude Code CLI, and the IDE extension. They do **not** run on the plain
> claude.ai web chat (no hook engine there); for that surface use the Caveau
> webapp (Mode B). The target CGP firm uses **Cowork**, which is fully covered.

## Why it's safe by design

- **Fail-closed.** A `PreToolUse` `deny` blocks the tool **even under
  `bypassPermissions` / `--dangerously-skip-permissions`** (per Claude Code
  docs). If the config is missing/malformed or the event is unparseable, the
  guard **denies** rather than waving data through.
- **100% local.** Pure-stdlib Python, no network, no telemetry. It only gates;
  anonymisation is the Caveau engine, also local.
- **Configurable.** Protected folders, allow-listed sub-paths, exempt
  extensions, and Bash scanning are all set per deployment.

## Install (one command, from the Bubble marketplace)

```
/plugin marketplace add vdk888/caveau-guard
/plugin install caveau-guard@caveau-guard
```

Or test locally without installing:

```bash
claude --plugin-dir /path/to/caveau/plugin/caveau-guard
```

After install, run `/reload-plugins` (or restart the session) to load the hook.

## Configure

**Cowork (recommended): drop a marker file in the client folder.** Put a
`.caveau-guard.json` (copy `config/marker.example.json`, or just `{}`) **inside**
any folder you want protected. The guard walks up from each accessed file to find
the nearest marker, so that folder + everything under it is guarded. This is the
only method that works in Cowork, which is sandboxed and can't write to
`~/.config` — but can write into a folder you've connected. Same idea as
`.gitignore`. Delete the marker to stop protecting the folder.

```jsonc
// <client-folder>/.caveau-guard.json
{ "allow_paths": ["clean"], "allow_extensions": [".anon.txt"], "block_bash": true }
```

**CLI fallback (optional):** a global config with a `protected_folders` list,
found in this order (first hit wins). It composes with markers.

1. `$CAVEAU_GUARD_CONFIG` (explicit path)
2. `<project>/.caveau-guard.json`
3. `~/.config/caveau/caveau-guard.json`
4. `~/.caveau-guard.json`
5. `<plugin>/config/caveau-guard.json` (packaged default)

```json
{
  "protected_folders": ["~/Dossiers-clients", "~/Downloads/souscriptions"],
  "allow_paths": ["~/Dossiers-clients/dossier-x/clean"],
  "allow_extensions": [".anon.txt"],
  "block_bash": true,
  "message_fr": "🔒 Caveau — accès bloqué…"
}
```

| Key | Meaning |
|---|---|
| `protected_folders` | Folders whose contents are blocked (recursive). The "coffre". |
| `allow_paths` | Specific paths inside a protected folder that are allowed (e.g. a `clean/` output dir). |
| `allow_extensions` | Extensions exempt inside protected folders (e.g. `.anon.txt` for cloaked output). |
| `block_bash` | Also deny Bash commands that mention a protected path (stops `cat …` bypassing the Read guard). |
| `message_fr` | The message Claude (and the user) sees when blocked. |

## How it works

1. `hooks/hooks.json` registers a `PreToolUse` hook on the file/shell tools.
2. `scripts/guard.py` reads the event JSON, resolves the target path(s), and
   compares against `protected_folders` (symlink-resolved, `~`-expanded).
3. Inside a protected folder (and not exempted) → `permissionDecision: "deny"`
   with a French message pointing to the `caveau-anonymize` skill.
4. The bundled skill `/caveau-guard:caveau-anonymize` is the sanctioned path:
   anonymise the folder into `clean/`, work on the cloaked copy, de-anonymise
   the answer locally with the vault.

### The chat box (tripwire)

The folder guard protects files **on disk**. A document **pasted or uploaded
directly into the chat** is injected into the model's context *before any tool
call*, so the `PreToolUse` guard never sees it — a platform limit, not a Caveau
choice. A second hook (`scripts/tripwire.py`, on `UserPromptSubmit`) covers
that path: it scans the prompt text for raw PII (IBAN, email, n° sécu, phone)
or attachment phrasing and **nudges Claude to redirect you to the protected
folder** (or hard-blocks, if `tripwire_block: true`).

> **Work from the folder, not the chat.** Put client documents in your
> protected folder (e.g. your Dropbox client sub-folder) and ask about them
> there — that's where Caveau anonymises for real. The tripwire is only a
> guard-rail for raw data that slips into the conversation.

## Test

```bash
python3 scripts/test_guard.py        # 14 black-box cases (deny/allow/fail-closed)
python3 scripts/test_tripwire.py     # 18 black-box cases (nudge/block/no-op/fail-open)
```

Verified end-to-end in a live Claude session: a raw PII file is blocked; an
`.anon.txt` copy is readable; the tripwire nudges on pasted IBAN/email.

## RGPD

Pseudonymisation **réversible** locale — mesure de sécurité (art. 25 & 32). Ne
remplace pas le DPA, l'AIPD, ni la relecture humaine. Voir `COMPLIANCE_RGPD.md`.
