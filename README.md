# openstax-convert

Convert an [OpenStax](https://openstax.org/) book repository (POET/CNXML format)
into a single block-segmented `book.json` — plus only the images the book
actually references — ready to drop into a reader, study tool, or
retrieval/grounding (RAG) pipeline.

OpenStax ships its books as git repos: a `collections/<slug>.collection.xml`
defines the spine (chapters and section order), each section is a
`modules/<id>/index.cnxml` file, and figures live in `media/`. That's great for
publishing but awkward for an app. This tool flattens it into one JSON file an
app can load directly.

It's a single, dependency-free Python 3 script (standard library only — no `pip
install`). It has been validated on multiple OpenStax books (Entrepreneurship,
Microbiology), which differ in note types, structure, and chapter counts.

## Quick start

```bash
# 1. Get a book repo (shallow clone is fine; repos are large because of media)
git clone --depth 1 https://github.com/openstax/osbooks-entrepreneurship.git book-repo

# 2. Convert
python3 scripts/convert.py --repo book-repo --out output
```

This writes `output/book.json` and `output/media/`, and prints a summary:

```
book: Entrepreneurship  (Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International)
collection: entrepreneurship.collection.xml
chapters: 15  sections: 79 (numbered: 62, intros/frontmatter: 17)
blocks: 810
media referenced: 217  copied: 217  missing: 0
book.json: 4262 KB  ->  /path/to/output
```

### Text only (skip the large media download)

For a quick RAG corpus where you don't need images, sparse-clone just the text:

```bash
git clone --depth 1 --filter=blob:none --no-checkout \
  https://github.com/openstax/osbooks-microbiology.git book-repo
cd book-repo && git sparse-checkout set collections modules && git checkout && cd ..
python3 scripts/convert.py --repo book-repo --out output
```

(Media will report as "missing" — expected, since it wasn't checked out.)

## Options

| Flag | Purpose |
|------|---------|
| `--repo` | Path to the OpenStax book repo root (required) |
| `--out` | Output directory for `book.json` + `media/` (required) |
| `--collection` | Collection name — only needed for multi-volume books (e.g. Calculus vol 1/2/3); the script lists the choices if there's more than one |
| `--budget` | Max tokens per block (default 2500). Lower for very small context windows; raise for fewer, larger blocks |
| `--media-base` | Absolute URL where the book's `media/` files are served, stamped into `book.json` as `media_base`. Lets `book.json` travel without its media — e.g. point it at the source repo itself: `https://raw.githubusercontent.com/openstax/<repo>/main/media/` |

## Output format

The **block** is the key unit. Whole sections are often far too big to fit a
small model's context window (some run 10k–19k tokens), so each section is cut
at heading boundaries into budget-sized blocks. A block carries both clean HTML
(for rendering) and flattened text (for prompting), so one artifact serves both
a reader and a retrieval pipeline.

```
book.json
├── title, publisher, license, license_url, slug
├── toc[]        — flat: chapter headings + {id, number, title} per section
└── sections[]
    ├── id, kind ("section" | "introduction" | "frontmatter"…)
    ├── chapter, number ("3.2"), title, objectives[], figures[], tokens
    ├── footnotes[]  — {id "fnN", html} per footnote, in document order
    └── blocks[]
        ├── anchor   — globally unique id, e.g. "m71114-b4"
        │              (use as a DOM id / scroll anchor and grounding handle)
        ├── trail[]  — heading breadcrumb, e.g. ["Operations Management","Money"]
        ├── heading, html, text
        └── tokens   — ~chars/4 estimate
```

Figure handling: image `src` is rewritten to `media/<file>`, and each figure's
alt text + caption are folded into the block `text`, so a text-only model can
answer questions about images it can't see. CNXML notes, tables, glossaries,
lists, and MathML are mapped to sensible HTML.

Footnote handling: CNXML `<footnote>` elements (typically full bibliography
citations) are NOT rendered inline — that would dump citation text into the
middle of the reading flow and into the plain-text `text` fields that feed
retrieval prompts. Instead each becomes a numbered superscript marker in the
block html (`<sup class="footnote-ref"><a id="fnrefN" href="#fnN">N</a></sup>`)
and the content is collected into the section-level `footnotes[]` list, for a
reader to render at the bottom of the page (link `#fnN` ↔ backlink `#fnrefN`).
Footnotes are stripped entirely from trails, headings, and objectives.

## Use as a Claude skill

This repo doubles as a [Claude](https://claude.com/claude-code) skill: `SKILL.md`
plus `scripts/` is the standard skill layout. Drop the folder into your skills
directory (e.g. `~/.claude/skills/openstax-convert/`) and invoke it with
`/openstax-convert`.

## Licensing of the converted content

OpenStax content is openly licensed, but the specific license varies by book
(commonly CC BY or CC BY-NC-SA). The script copies whatever the book's
collection declares into `book.json`. If you publish the output, surface
attribution to OpenStax and the authors, and honor the NonCommercial /
ShareAlike terms when the book carries them.

## License

This tool (the converter code in this repo) is released under the
[MIT License](LICENSE). Note this is separate from the license of any book
content you convert with it — see the section above.
