#!/usr/bin/env python3
"""Convert an OpenStax book repo (POET/CNXML format) into a block-segmented
JSON content store for grounded reading / RAG apps.

Reads:  <repo>/collections/<slug>.collection.xml   (the book spine)
        <repo>/modules/<id>/index.cnxml             (one section each)
        <repo>/media/*                              (figures)

Writes: <out>/book.json   - book metadata + ordered sections + TOC, where each
                            section is split into budget-sized "blocks"
        <out>/media/*      - only the images actually referenced by the book

Each section: { id, kind, chapter, number, title, objectives[], figures[],
                tokens, blocks[] }; each block:
              { anchor, trail[], heading, html, text, tokens }.

The block is the addressable unit for both rendering and grounding: small
enough to fit a small model's context window, aligned to heading boundaries.

Usage:  python convert.py --repo <openstax-repo> --out <output-dir>
                          [--collection <name>] [--budget 2500]

Stdlib only (xml.etree + html.parser); no third-party deps.
"""

import argparse
import json
import re
import shutil
import sys
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

# Set from CLI args in main(); the conversion functions read these globals.
SRC = MODULES = MEDIA_SRC = OUT = MEDIA_OUT = None

CNX = "{http://cnx.rice.edu/cnxml}"
MD = "{http://cnx.rice.edu/mdml}"
COL = "{http://cnx.rice.edu/collxml}"

# OpenStax note callout classes vary by book (entrepreneurship has
# "work-it-out", biology has "check-your-understanding", etc.), so derive a
# readable label from the class name rather than hardcoding a per-book map. A
# note's own <title> takes precedence when present.
def humanize(slug):
    return re.sub(r"[-_]+", " ", slug or "").strip().capitalize()

EMPHASIS = {"italics": "em", "bold": "strong", "underline": "u",
            "smallcaps": "span", "normal": "span"}

referenced_media = set()


def local(tag):
    """Strip the XML namespace from a tag name."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def inner(el, level):
    """Render an element's mixed content: text, children, and child tails."""
    parts = []
    if el.text:
        parts.append(escape(el.text))
    for child in el:
        parts.append(render(child, level))
        if child.tail:
            parts.append(escape(child.tail))
    return "".join(parts)


def find_title(el):
    for c in el:
        if local(c.tag) == "title":
            return inner(c, 0).strip()
    return ""


def render_figure(el, level):
    src = alt = ""
    caption = ""
    for node in el.iter():
        t = local(node.tag)
        if t == "image" and node.get("src"):
            src = node.get("src")
        elif t == "media" and node.get("alt"):
            alt = node.get("alt")
        elif t == "caption":
            caption = inner(node, level).strip()
    fname = re.sub(r"^.*/", "", src)
    if fname:
        referenced_media.add(fname)
    img = f'<img src="media/{escape(fname)}" alt="{escape(alt)}">' if fname else ""
    cap = f"<figcaption>{caption}</figcaption>" if caption else ""
    # alt repeated in a hidden span so it lands in the grounding text too.
    alt_text = f'<span class="figure-alt">{escape(alt)}</span>' if alt else ""
    return f'<figure>{img}{cap}{alt_text}</figure>'


def render_table(el, level):
    out = ["<table>"]
    for part in el.iter():
        pass
    # tgroup holds thead/tbody; rows hold entries.
    def rows(container, cell):
        r = []
        for row in container:
            if local(row.tag) != "row":
                continue
            cells = "".join(
                f"<{cell}>{inner(e, level)}</{cell}>"
                for e in row if local(e.tag) == "entry")
            r.append(f"<tr>{cells}</tr>")
        return "".join(r)

    for tgroup in el:
        if local(tgroup.tag) != "tgroup":
            continue
        for sec in tgroup:
            t = local(sec.tag)
            if t == "thead":
                out.append("<thead>" + rows(sec, "th") + "</thead>")
            elif t == "tbody":
                out.append("<tbody>" + rows(sec, "td") + "</tbody>")
    out.append("</table>")
    return "".join(out)


def render_glossary(el, level):
    items = []
    for d in el:
        if local(d.tag) != "definition":
            continue
        term = meaning = ""
        for c in d:
            if local(c.tag) == "term":
                term = inner(c, level).strip()
            elif local(c.tag) == "meaning":
                meaning = inner(c, level).strip()
        items.append(f"<dt>{term}</dt><dd>{meaning}</dd>")
    return f'<dl class="glossary">{"".join(items)}</dl>' if items else ""


def render(el, level):
    """Return the HTML string for a single CNXML element."""
    t = local(el.tag)

    if t == "para":
        return f"<p>{inner(el, level)}</p>"
    if t == "emphasis":
        tag = EMPHASIS.get(el.get("effect", "italics"), "em")
        return f"<{tag}>{inner(el, level)}</{tag}>"
    if t == "term":
        return f"<dfn>{inner(el, level)}</dfn>"
    if t == "link":
        url = el.get("url")
        text = inner(el, level).strip()
        if url:
            return f'<a href="{escape(url)}" rel="noopener" target="_blank">{text or escape(url)}</a>'
        return text  # unresolved internal cross-reference -> keep text, drop link
    if t == "list":
        tag = "ol" if el.get("list-type") == "enumerated" else "ul"
        lis = "".join(render(c, level) for c in el if local(c.tag) == "item")
        return f"<{tag}>{lis}</{tag}>"
    if t == "item":
        return f"<li>{inner(el, level)}</li>"
    if t == "section":
        h = min(level, 6)
        title = find_title(el)
        body = "".join(
            render(c, level + 1) for c in el if local(c.tag) != "title")
        cls = el.get("class", "")
        head = f"<h{h}>{title}</h{h}>" if title else ""
        return f'<section class="{escape(cls)}">{head}{body}</section>'
    if t == "figure":
        return render_figure(el, level)
    if t == "note":
        cls = el.get("class", "")
        label = find_title(el) or humanize(cls) or "Note"
        body = "".join(
            render(c, level) for c in el if local(c.tag) != "title")
        return (f'<aside class="note note-{escape(cls)}">'
                f'<p class="note-label">{escape(label)}</p>{body}</aside>')
    if t == "table":
        return render_table(el, level)
    if t == "glossary":
        return render_glossary(el, level)
    if t in ("exercise", "problem"):
        body = "".join(render(c, level) for c in el)
        return f'<div class="{t}">{body}</div>'
    if t == "footnote":
        return f'<span class="footnote">{inner(el, level)}</span>'
    if t == "quote":
        return f"<blockquote>{inner(el, level)}</blockquote>"
    if t == "newline":
        return "<br>"
    if t in ("sup", "sub"):
        return f"<{t}>{inner(el, level)}</{t}>"
    if t == "math":  # MathML, rare; pass through for native browser rendering
        return ET.tostring(el, encoding="unicode")
    if t in ("title", "metadata", "label", "colspec"):
        return ""  # handled elsewhere or non-rendering
    # default: unwrap and keep contents
    return inner(el, level)


class TextExtractor(HTMLParser):
    """Flatten rendered HTML to plain text for the grounding prompt,
    including image alt text and block spacing."""
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6",
             "figcaption", "dt", "dd", "tr", "aside", "blockquote", "br"}

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.parts.append(f" [figure: {alt}] ")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        return raw.strip()


def html_to_text(html):
    ex = TextExtractor()
    ex.feed(html)
    return ex.text()


BLOCK_BUDGET = 2500  # target max tokens per grounding/render block


def toks(el):
    return len("".join(el.itertext())) // 4


def make_block(els, trail, anchor, heading=None, level=2):
    """Build a block record from a list of CNXML elements."""
    head_html = f"<h{level}>{escape(heading)}</h{level}>" if heading else ""
    html = head_html + "".join(render(e, level) for e in els)
    text = html_to_text(html)
    return {
        "anchor": anchor,
        "trail": trail,
        "heading": heading or (trail[-1] if trail else None),
        "html": html,
        "text": text,
        "tokens": len(text) // 4,
    }


def chunk_paras(els, trail, anchor_base, heading, level):
    """Fallback: a too-big section with no sub-sections — split by paragraphs."""
    blocks, buf, n = [], [], 0
    size = 0
    head = heading
    for el in els:
        buf.append(el)
        size += toks(el)
        if size >= BLOCK_BUDGET:
            blocks.append(make_block(buf, trail, f"{anchor_base}-p{n}", head, level))
            buf, size, n, head = [], 0, n + 1, None  # heading only on first chunk
    if buf:
        blocks.append(make_block(buf, trail, f"{anchor_base}-p{n}", head, level))
    return blocks


def segment(elements, trail, counter, level=2):
    """Split a run of CNXML elements into budget-sized, addressable blocks.

    counter is a single-item list used as a shared mutable id sequence so anchors
    are unique within a module."""
    blocks, buffer = [], []

    def next_anchor():
        counter[0] += 1
        return f"b{counter[0]}"

    def emit_leaf(els, tr, heading=None):
        """Emit one block, or several paragraph-chunks if over budget."""
        anchor = next_anchor()
        if sum(toks(e) for e in els) <= BLOCK_BUDGET:
            blocks.append(make_block(els, tr, anchor, heading=heading, level=level))
        else:
            blocks.extend(chunk_paras(els, tr, anchor, heading, level))

    def flush_buffer():
        if buffer:
            emit_leaf(list(buffer), trail)
            buffer.clear()

    for el in elements:
        if local(el.tag) != "section":
            buffer.append(el)
            continue
        flush_buffer()
        title = find_title(el)
        if toks(el) <= BLOCK_BUDGET:
            # render(el) already emits the section's own <h> title; don't
            # prepend another via the heading arg.
            blocks.append(make_block([el], trail + [title], next_anchor(),
                                     level=level))
            continue
        # too big: peel off heading+intro, then recurse into nested sections
        new_trail = trail + [title]
        child_secs = [c for c in el if local(c.tag) == "section"]
        intro = [c for c in el if local(c.tag) not in ("section", "title")]
        if child_secs:
            if intro:
                emit_leaf(intro, new_trail, heading=title)
            blocks += segment(child_secs, new_trail, counter, level + 1)
        else:
            blocks += chunk_paras(intro, new_trail, next_anchor(), title, level)
    flush_buffer()
    return blocks


def extract_objectives(content_el, level):
    for sec in content_el.iter():
        if local(sec.tag) == "section" and sec.get("class") == "learning-objectives":
            objs = []
            for item in sec.iter():
                if local(item.tag) == "item":
                    objs.append(inner(item, level).strip())
            return objs
    return []


def collect_figures(content_el, level):
    figs = []
    for fig in content_el.iter():
        if local(fig.tag) != "figure":
            continue
        src = alt = caption = ""
        for node in fig.iter():
            tt = local(node.tag)
            if tt == "image" and node.get("src"):
                src = re.sub(r"^.*/", "", node.get("src"))
            elif tt == "media" and node.get("alt"):
                alt = node.get("alt")
            elif tt == "caption":
                caption = inner(node, level).strip()
        figs.append({"src": src, "alt": alt, "caption": caption})
    return figs


def convert_module(mod_id):
    path = MODULES / mod_id / "index.cnxml"
    tree = ET.parse(path)
    doc = tree.getroot()
    doc_class = doc.get("class", "")

    title = ""
    for meta in doc:
        if local(meta.tag) == "metadata":
            for m in meta:
                if local(m.tag) == "title":
                    title = (m.text or "").strip()

    content_el = next(c for c in doc if local(c.tag) == "content")
    blocks = segment(list(content_el), [], counter=[0], level=2)
    # prefix anchors with the module id so they are globally unique
    for b in blocks:
        b["anchor"] = f"{mod_id}-{b['anchor']}"

    return {
        "id": mod_id,
        "doc_class": doc_class,
        "title": title,
        "objectives": extract_objectives(content_el, 1),
        "figures": collect_figures(content_el, 1),
        "tokens": sum(b["tokens"] for b in blocks),
        "blocks": blocks,
    }


def collection_metadata(coll):
    """Pull book title + license out of the collection's <metadata>."""
    meta = {"title": "", "license": "", "license_url": "", "slug": ""}
    for node in coll:
        if local(node.tag) != "metadata":
            continue
        for m in node:
            tag = local(m.tag)
            if tag == "title":
                meta["title"] = (m.text or "").strip()
            elif tag == "slug":
                meta["slug"] = (m.text or "").strip()
            elif tag == "license":
                meta["license"] = (m.text or "").strip()
                meta["license_url"] = m.get("url", "")
    return meta


def build(collection_path):
    coll = ET.parse(collection_path).getroot()
    content = next(c for c in coll if local(c.tag) == "content")
    meta = collection_metadata(coll)

    sections = []
    toc = []
    chapter_no = 0

    def title_of(node):
        for c in node:
            if local(c.tag) == "title":
                return (c.text or "").strip()
        return ""

    for node in content:
        t = local(node.tag)
        if t == "module":
            # top-level module (preface / appendix), no chapter
            rec = convert_module(node.get("document"))
            rec.update(kind=rec["doc_class"] or "frontmatter",
                       chapter=None, number=None)
            sections.append(rec)
            toc.append({"id": rec["id"], "number": None,
                        "title": rec["title"], "chapter": None})
        elif t == "subcollection":
            chapter_no += 1
            ch_title = title_of(node)
            ch_content = next(c for c in node if local(c.tag) == "content")
            sec_no = 0
            toc.append({"chapter": chapter_no, "title": ch_title, "heading": True})
            for m in ch_content:
                if local(m.tag) != "module":
                    continue
                rec = convert_module(m.get("document"))
                if rec["doc_class"] == "introduction":
                    number = None
                    kind = "introduction"
                else:
                    sec_no += 1
                    number = f"{chapter_no}.{sec_no}"
                    kind = "section"
                rec.update(kind=kind, chapter=chapter_no, number=number)
                sections.append(rec)
                toc.append({"id": rec["id"], "number": number,
                            "title": rec["title"], "chapter": chapter_no})

    return sections, toc, meta


def find_collection(src, name):
    """Locate the collection XML. A repo usually has one; multi-volume books
    (e.g. calculus-volume-1/-2/-3) have several, so require --collection then."""
    colls = sorted((src / "collections").glob("*.collection.xml"))
    if not colls:
        sys.exit(f"error: no collections/*.collection.xml under {src} — "
                 "is this an OpenStax book repo?")
    if name:
        match = [c for c in colls if c.name == name or c.stem.replace(".collection", "") == name]
        if not match:
            sys.exit(f"error: collection '{name}' not found. Available: "
                     + ", ".join(c.name for c in colls))
        return match[0]
    if len(colls) > 1:
        sys.exit("error: multiple collections found; pass --collection <name>:\n  "
                 + "\n  ".join(c.name for c in colls))
    return colls[0]


def main():
    global SRC, MODULES, MEDIA_SRC, OUT, MEDIA_OUT, BLOCK_BUDGET
    ap = argparse.ArgumentParser(description="Convert an OpenStax CNXML book to book.json.")
    ap.add_argument("--repo", required=True, help="path to the OpenStax book repo root")
    ap.add_argument("--out", required=True, help="output directory for book.json + media/")
    ap.add_argument("--collection", default="",
                    help="collection name (only needed if the repo has several)")
    ap.add_argument("--budget", type=int, default=BLOCK_BUDGET,
                    help=f"max tokens per block (default {BLOCK_BUDGET})")
    args = ap.parse_args()

    SRC = Path(args.repo).resolve()
    MODULES = SRC / "modules"
    MEDIA_SRC = SRC / "media"
    OUT = Path(args.out).resolve()
    MEDIA_OUT = OUT / "media"
    BLOCK_BUDGET = args.budget

    if not MODULES.is_dir():
        sys.exit(f"error: {MODULES} not found — point --repo at the repo root.")
    collection_path = find_collection(SRC, args.collection)

    OUT.mkdir(parents=True, exist_ok=True)
    MEDIA_OUT.mkdir(parents=True, exist_ok=True)

    sections, toc, meta = build(collection_path)

    book = {
        "title": meta["title"] or SRC.name,
        "publisher": "OpenStax",
        "license": meta["license"],
        "license_url": meta["license_url"],
        "slug": meta["slug"],
        "sections": sections,
        "toc": toc,
    }
    (OUT / "book.json").write_text(json.dumps(book, ensure_ascii=False, indent=2))

    copied = missing = 0
    for fname in sorted(referenced_media):
        src = MEDIA_SRC / fname
        if src.exists():
            shutil.copy2(src, MEDIA_OUT / fname)
            copied += 1
        else:
            missing += 1

    chapters = sum(1 for x in toc if x.get("heading"))
    numbered = sum(1 for s in sections if s["number"])
    blocks = sum(len(s["blocks"]) for s in sections)
    print(f"book: {book['title']}  ({book['license'] or 'license: unknown'})")
    print(f"collection: {collection_path.name}")
    print(f"chapters: {chapters}  sections: {len(sections)} "
          f"(numbered: {numbered}, intros/frontmatter: {len(sections) - numbered})")
    print(f"blocks: {blocks}")
    print(f"media referenced: {len(referenced_media)}  copied: {copied}  missing: {missing}")
    print(f"book.json: {(OUT / 'book.json').stat().st_size // 1024} KB  ->  {OUT}")


if __name__ == "__main__":
    main()
