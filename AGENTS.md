# AGENTS.md — TA-taxonomies

The **taxonomy layer** of Talent Angels: one suite per taxonomy (loader + graph
schema + tools) behind the shared suite contract that `TA-agents` consumes as a
versioned library.

This file is the source of truth for humans and for every AI coding agent
(Claude Code, Codex, Cursor, Antigravity, Gemini, Aider, and any other).
`CLAUDE.md` is a one-line import of this file.

This is a **subrepo** of the Talent Angels workspace.

## Read first

1. The workspace policy: `../AGENTS.md`
   (or https://github.com/LFX-Talent-Angels/TA-workspace → `AGENTS.md`).
   It is **authoritative** — branch flow, DCO, secrets, agent conventions.
2. `../docs/architecture/SYSTEM.md` — cross-repo architecture + suite contract.
3. `ARCHITECTURE.md` in this repo — suite internals, ingestion pipeline,
   licensing rules. **Follow it.**
4. This file for code-specific rules.

## What lives here

```
src/ta_taxonomies/
├── contract/     # the typed tool surface — the ONLY thing TA-agents imports
├── suites/       # esco/ onet/ sfia/ bls/ — one owner (mentee) per suite
├── crosswalks/   # explicit cross-taxonomy links only
└── ingestion/    # shared fetch → normalize → load → validate helpers
tests/            # contract tests + load validation, run on fixtures
```

Agent/assistant code **never** lives here — that's `TA-agents`.

## Hard rules (short form — full text in ARCHITECTURE.md)

- **Pointer, not payload**: never commit licensed source text or dumps. This
  repo is Apache-2.0; the sources did not grant us redistribution rights.
  Fixtures are small, license-clean subsets.
- IDs are **suite-scoped** (`esco:…`); every node carries `source` +
  `source_id`. Codes stay strings (leading zeros!).
- Loaders are **reproducible** (`python -m ta_taxonomies.suites.<x>.load`) and
  end with validation assertions (no dangling edges, counts survive).
- `MERGE` on identity, never on bare values; attach values as properties.
- Cross-suite links live in `crosswalks/` only — explicit and cited; when no
  reliable link exists the answer is "no link", not a guess.
- Suites never import each other.

## Conventions

- **Python 3.11+.** Package is `ta_taxonomies`, src-layout (`src/`).
- Formatting/linting: **ruff**; types: **mypy**.
- Tests: **pytest**. New suite behavior ships with contract tests on fixtures.
- Configuration via env vars — see `.env.example`. **Never** commit `.env`.

```bash
pytest                 # contract tests on fixtures
ruff check .           # lint
ruff format .          # format
mypy src               # type-check
docker compose up -d   # local Neo4j for the loaders
```

## Branches and pull requests

| Branch | What it is        | To merge into it                            |
| ------ | ----------------- | ------------------------------------------- |
| `dev`  | Integration trunk | 1 approval from any contributor + green CI  |
| `main` | What we publish   | 1 approval **from a code owner** + green CI |

- **Open every pull request against `dev`.** `main` only receives promotions
  from `dev`.
- Never push directly to `dev` or `main`.
- **Every commit signed off**: `git commit -s` (DCO). Pull requests without it
  are blocked.
- Without write access, fork and open the pull request from your fork. With
  write access, push the branch to this repo directly, which is what makes
  stacked pull requests possible (`gh stack init --trunk dev`).
- One suite = one owner. Changes to `contract/` need a **code owner review**,
  because they ripple into `TA-agents`. See `.github/CODEOWNERS`.

Full details in the workspace `AGENTS.md` and `CONTRIBUTING.md`.

## AI agents

Read this file and `ARCHITECTURE.md` before changing code. Review and test agent
output; you own what you submit. Record non-obvious decisions in `TA-memory`.
