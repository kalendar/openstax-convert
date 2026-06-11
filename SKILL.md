---
name: openstax-convert
description: Convert an OpenStax book repository (POET/CNXML format) into a block-segmented book.json content store (plus copied media) for grounded reading and RAG apps. Use this skill whenever the user invokes "/openstax-convert", points at an OpenStax book repo (an osbooks-* GitHub repo, or a local folder with collections/ + modules/ + media/) and wants to turn it into JSON, asks to parse/convert/import CNXML, prepare an OpenStax textbook for a reader, tutor, or retrieval app, or mentions wanting an OpenStax book as structured/chunked data. Also use it when the user references book.json in the context of OpenStax content. Trigger even if they don't say "CNXML" — any "OpenStax book → usable data" request fits.
version: 1.0.0
---

# /openstax-convert — OpenStax CNXML book → block-segmented book.json

OpenStax publishes its books as git repos in the POET/CNXML format: a
`collections/<slug>.collection.xml` file defines the book spine (chapters and
their section order), each section is a `modules/<id>/index.cnxml` file, and
figures live in `media/`. That layout is great for publishing but not for an
app — you can't drop a CNXML tree into a reader or a retrieval pipeline.

This skill runs `scripts/convert.py`, which turns the repo into a single
`book.json` (plus only the images the book actually references) that an app can
load directly. It's stdlib-only Python 3 — no pip installs.

The script is the reference implementation behind the LGOER reader; it has been
validated on multiple OpenStax books (Entrepreneurship, Microbiology), which
differ in note types, structure, and chapter counts.

## When to reach for this

Any time someone wants an OpenStax book as structured data: building a reader,
a study tool, a RAG/grounding corpus, search, or analysis. The output is
designed so the same artifact serves both **rendering** (each block carries
clean HTML) and **grounding** (each block carries plain text sized to fit a
small model's context window).

## How to run it

### 1. Get the book repo

If the user gave a local path, use it. Otherwise clone from OpenStax's GitHub
(repos are named `osbooks-<slug>`). The repos are large because of media, so a
shallow clone is usually right:

```bash
git clone --depth 1 https://github.com/openstax/osbooks-<slug>.git <repo-dir>
```

If you only need the structured text and not the images (e.g. a quick RAG
corpus), skip the media download with a sparse clone — much faster:

```bash
git clone --depth 1 --filter=blob:none --no-checkout https://github.com/openstax/osbooks-<slug>.git <repo-dir>
cd <repo-dir> && git sparse-checkout set collections modules && git checkout
```

### 2. Convert

```bash
python3 scripts/convert.py --repo <repo-dir> --out <output-dir>
```

This writes `<output-dir>/book.json` and `<output-dir>/media/` (only the
referenced images). The script prints a summary — book title, license, chapter
/ section / block counts, and how many media files copied vs. missing. Relay
that summary to the user so they can sanity-check it.

**Options:**
- `--collection <name>` — only needed for multi-volume books (e.g. Calculus
  has `calculus-volume-1.collection.xml`, `-2`, `-3`). The script errors and
  lists the choices if there's more than one and you didn't pick.
- `--budget <tokens>` — max tokens per block (default 2500). Lower it if you're
  targeting a model with a very small context window; raise it for fewer, larger
  blocks.

A few hundred missing media files usually means a sparse/partial clone (the
images weren't checked out) — fine if the user didn't want images. If media was
expected, re-clone without the sparse filter.

## What it produces

`book.json` has book metadata (title + license read straight from the
collection), a flat `toc`, and an ordered list of `sections`. Each section is
split into **blocks** — the key idea, since whole sections are often far too big
to stuff into a small model's context (some run 10k–19k tokens). Blocks are cut
at heading boundaries and capped at the token budget, so they're the right unit
for both rendering a page and grounding an answer.

```
book.json
├── title, publisher, license, license_url, slug
├── toc[]        — flat: chapter headings + {id, number, title} per section
└── sections[]
    ├── id, kind ("section" | "introduction" | "frontmatter"…)
    ├── chapter, number ("3.2"), title, objectives[], figures[], tokens
    └── blocks[]
        ├── anchor   — globally unique id, e.g. "m71114-b4" (use as a DOM id /
        │              scroll anchor and as the grounding handle)
        ├── trail[]  — heading breadcrumb, e.g. ["Operations Management","Money"]
        ├── heading, html (clean semantic HTML), text (flattened, for prompts)
        └── tokens   — ~chars/4 estimate
```

Figure handling worth knowing: image `src` is rewritten to `media/<file>`, and
each figure's alt text + caption are folded into the block `text`, so a
text-only model can answer questions about images it can't see. CNXML notes,
tables, glossaries, lists, and MathML are all mapped to sensible HTML; note
callout labels come from the note's own title when present, otherwise a
humanized form of its class (so it adapts to any book's note types).

## A note on licensing

OpenStax content is openly licensed but the specific license varies by book
(commonly CC BY or CC BY-NC-SA) — the script copies whatever the collection
declares into `book.json`. If the output will be published, make sure the app
surfaces attribution to OpenStax and the authors, and honors the NonCommercial /
ShareAlike terms when the book carries them.

## Extending the converter

`scripts/convert.py` is a single readable file. The CNXML→HTML transform is a
recursive `render()` dispatch on element tag; add a case there for any element
a particular book uses that isn't handled yet. Block segmentation lives in
`segment()` / `chunk_paras()`. It's intentionally dependency-free so it runs
anywhere Python 3 does.
