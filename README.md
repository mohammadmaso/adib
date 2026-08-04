# Adib

Desktop app that translates whole books — PDF, EPUB, DOCX, HTML, Markdown — into a
new, properly typeset PDF and EPUB in the target language. Built for RTL targets
(Persian, Arabic) as a first-class case, not an afterthought.

It parses the source into a semantic document tree, detects the book's tone and
proposes a professional translation style, builds a reviewable glossary of
technical terms with LLM-authored explanations, translates section by section with
consistent terminology, and re-typesets the result with images and tables in the
same reading positions.

## Architecture

```
Tauri shell (Rust)  ──spawns──>  adib-engine (Python sidecar)
  React + TS UI                    FastAPI + Pydantic AI
       │                                  │
       └────── HTTP + SSE ────────────────┘         ──> typst ──> PDF
          127.0.0.1:<ephemeral>                     ──> XHTML ──> EPUB
```

The engine binds an ephemeral port and announces it on stdout; Rust reads that
line, so there is no "pick a free port then bind it" race. Rust mints a per-run
token that every request must carry. The webview is granted no shell permissions —
the sidecar is spawned from Rust, which is not ACL-gated.

- `engine/` — Python engine (`adib_engine`): ingest, agents, glossary, renderers, store
- `apps/desktop/` — Tauri v2 + React 19 + Tailwind v4
- `packages/schema/` — TypeScript API types, generated from the engine's Pydantic models
- `scripts/` — binary preparation and type generation

## Setup

Requires Node 20+, Rust, and [uv](https://docs.astral.sh/uv/).

```sh
cd engine && uv sync            # Python deps
cd ../apps/desktop && npm install
cd ../.. && ./scripts/prepare-binaries.sh   # dev engine wrapper + typst binary
```

## Development

```sh
cd apps/desktop && npm run tauri dev
```

`prepare-binaries.sh` (without `--release`) writes a sidecar that shells out to
`uv run adib-engine`, so the app follows the same spawn-and-handshake path as
production while picking up Python edits without a rebuild. One caveat: killing
the shell kills the `uv` wrapper but not the Python process underneath it. The
engine's own heartbeat watchdog reclaims it within ~60s, so orphans clean
themselves up; a release build is a single frozen binary and has no such gap.

To attach to an engine you started yourself instead of spawning one:

```sh
cd engine && uv run adib-engine --port 8765 --auth-token dev
ADIB_ENGINE_URL=http://127.0.0.1:8765 ADIB_ENGINE_TOKEN=dev npm run tauri dev
```

### After changing the API or the Pydantic models

```sh
./scripts/generate-types.sh     # regenerates packages/schema/src/api.ts
```

The frontend never hand-writes request or response types.

### Tests

```sh
cd engine && uv run pytest
cd apps/desktop && npm run build        # tsc type-check + vite build
cd apps/desktop/src-tauri && cargo check
```
