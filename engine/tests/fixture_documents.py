"""Builders for golden-file ingest fixtures.

Each function writes a small but structurally complete document (headings,
prose, a table, a figure with caption, a list, code) to `dst` in its native
format, built programmatically so no binary fixtures live in the repo.
"""

from __future__ import annotations

from pathlib import Path

_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415478da6360600000000200015b21a4de0000000049454e44ae426082"
)


def write_epub(dst: Path) -> None:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("golden-epub")
    book.set_title("A Short Book About Networks")
    book.set_language("en")
    book.add_author("Ada Lovelace")

    chapter = epub.EpubHtml(title="Introduction", file_name="chap1.xhtml", lang="en")
    chapter.content = """
    <html><body>
    <h1>Introduction</h1>
    <p>Networks move <em>bytes</em> between machines that will never meet.</p>
    <h2>Layers</h2>
    <p>The stack is layered so each part can change independently.</p>
    <table>
      <tr><th>Layer</th><th>Purpose</th></tr>
      <tr><td>Transport</td><td>End-to-end delivery</td></tr>
      <tr><td>Network</td><td>Routing between hosts</td></tr>
    </table>
    <figure><img src="images/fig1.png" alt="Diagram"/>
      <figcaption>A packet in flight</figcaption></figure>
    <ul><li>one</li><li>two</li></ul>
    <pre>print('hello')</pre>
    <h1>Conclusion</h1>
    <p>That is all for now.</p>
    </body></html>
    """
    book.add_item(chapter)
    book.add_item(
        epub.EpubItem(
            uid="img1", file_name="images/fig1.png", media_type="image/png", content=_TINY_PNG
        )
    )
    book.toc = (epub.Link("chap1.xhtml", "Introduction", "intro"),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    epub.write_epub(str(dst), book)


def write_markdown(dst: Path) -> None:
    dst.write_text(
        """# Introduction

Networks move *bytes* between machines that will never meet.

## Layers

The stack is layered so each part can change independently.

| Layer | Purpose |
| --- | --- |
| Transport | End-to-end delivery |
| Network | Routing between hosts |

- one
- two

```python
print('hello')
```

# Conclusion

That is all for now.
""",
        encoding="utf-8",
    )


def write_txt(dst: Path) -> None:
    dst.write_text(
        """Introduction

Networks move bytes between machines that will never meet.

Layers

The stack is layered so each part can change independently.
""",
        encoding="utf-8",
    )


def write_html(dst: Path) -> None:
    dst.write_text(
        """<html><head><title>A Short Book About Networks</title></head>
<body>
<h1>Introduction</h1>
<p>Networks move <em>bytes</em> between machines that will never meet.</p>
<h2>Layers</h2>
<p>The stack is layered so each part can change independently.</p>
<table>
  <tr><th>Layer</th><th>Purpose</th></tr>
  <tr><td>Transport</td><td>End-to-end delivery</td></tr>
  <tr><td>Network</td><td>Routing between hosts</td></tr>
</table>
<figure><img src="fig1.png" alt="Diagram"/><figcaption>A packet in flight</figcaption></figure>
<ul><li>one</li><li>two</li></ul>
<pre>print('hello')</pre>
<h1>Conclusion</h1>
<p>That is all for now.</p>
</body></html>
""",
        encoding="utf-8",
    )
    (dst.parent / "fig1.png").write_bytes(_TINY_PNG)


def write_pdf(dst: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Introduction", fontsize=24)
    page.insert_text(
        (72, 110), "Networks move bytes between machines that will never meet.", fontsize=11
    )
    page.insert_text((72, 160), "Layers", fontsize=18)
    page.insert_text(
        (72, 190), "The stack is layered so each part can change independently.", fontsize=11
    )

    x0, y0, w, h = 72, 230, 300, 60
    page.draw_rect(pymupdf.Rect(x0, y0, x0 + w, y0 + h))
    page.draw_line((x0, y0 + 30), (x0 + w, y0 + 30))
    page.draw_line((x0 + 150, y0), (x0 + 150, y0 + h))
    page.insert_text((x0 + 10, y0 + 20), "Layer", fontsize=11)
    page.insert_text((x0 + 160, y0 + 20), "Purpose", fontsize=11)
    page.insert_text((x0 + 10, y0 + 50), "Transport", fontsize=11)
    page.insert_text((x0 + 160, y0 + 50), "End-to-end delivery", fontsize=11)

    page.insert_text((72, 330), "Conclusion", fontsize=24)
    page.insert_text((72, 360), "That is all for now.", fontsize=11)

    doc.save(str(dst))
    doc.close()


def write_pdf_with_toc(dst: Path) -> None:
    """A born-digital PDF whose first page is a printed "Contents" page."""
    import pymupdf

    doc = pymupdf.open()
    toc_page = doc.new_page()
    toc_page.insert_text((72, 72), "Contents", fontsize=24)
    entries = [
        "Introduction .......................... 1",
        "Layers ................................ 3",
        "Conclusion ............................ 8",
    ]
    y = 120
    for entry in entries:
        toc_page.insert_text((72, y), entry, fontsize=11)
        y += 20

    body_page = doc.new_page()
    body_page.insert_text((72, 72), "Introduction", fontsize=24)
    body_page.insert_text(
        (72, 110), "Networks move bytes between machines that will never meet.", fontsize=11
    )

    doc.save(str(dst))
    doc.close()


def write_epub_with_toc_page(dst: Path) -> None:
    """An EPUB carrying a human-readable "Contents" chapter in its spine, on
    top of the machine-generated `EpubNav` every EPUB already has."""
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("toc-page-epub")
    book.set_title("A Book With A Printed Contents Page")
    book.set_language("en")

    toc_chapter = epub.EpubHtml(title="Contents", file_name="toc.xhtml", lang="en")
    toc_chapter.content = """
    <html><body>
    <h1>Contents</h1>
    <ul>
      <li><a href="chap1.xhtml">Introduction</a></li>
      <li><a href="chap1.xhtml#layers">Layers</a></li>
      <li><a href="chap1.xhtml#conclusion">Conclusion</a></li>
    </ul>
    </body></html>
    """
    book.add_item(toc_chapter)

    chapter = epub.EpubHtml(title="Introduction", file_name="chap1.xhtml", lang="en")
    chapter.content = """
    <html><body>
    <h1>Introduction</h1>
    <p>Networks move bytes between machines that will never meet.</p>
    </body></html>
    """
    book.add_item(chapter)

    book.toc = (epub.Link("chap1.xhtml", "Introduction", "intro"),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", toc_chapter, chapter]
    epub.write_epub(str(dst), book)


def write_scanned_pdf(dst: Path) -> None:
    """A PDF with no text layer at all, like a raw scan."""
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(dst))
    doc.close()
