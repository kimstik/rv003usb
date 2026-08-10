"""mdlite.py — a deliberately small Markdown -> HTML renderer.

Scope is exactly what the project's REPORT.md files use: ATX headings, fenced
code blocks, pipe tables (with the ---|--- separator row), unordered and
ordered lists (one nesting level, as written), blockquotes, paragraphs, and
the inline set `code`, **bold**, *italic*, and links.

Links are FLATTENED by design (the index is a single offline file, so an href
into a sibling directory would be a dead click): the link text is kept, and a
bare in-repo path is shown after it in a muted span when it differs from the
text.  Nothing else is interpreted, and any HTML in the source is escaped.
"""
import html
import re

_INLINE_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITAL = re.compile(r"(?<![*\w])\*([^*]+)\*(?![*\w])")


def _inline(s):
    s = html.escape(s)
    holes = []

    def stash(m):
        holes.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(holes) - 1}\x00"

    s = _INLINE_CODE.sub(stash, s)

    def link(m):
        text, target = m.group(1), m.group(2)
        text = text if text.strip() else target
        if target and target not in text and not target.startswith("#"):
            return (f"{text} <span class=\"lnk\">[{target}]</span>")
        return text

    s = _LINK.sub(link, s)
    s = _BOLD.sub(r"<b>\1</b>", s)
    s = _ITAL.sub(r"<i>\1</i>", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: holes[int(m.group(1))], s)
    return s


def _is_table_sep(line):
    return bool(re.fullmatch(r"\s*\|?[\s:|-]*-[\s:|-]*\|?\s*", line)) and \
        "-" in line and "|" in line


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def render(md, hshift=2):
    """Markdown -> HTML fragment.  hshift pushes '# ' down to h(1+hshift)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]

        if line.startswith("```"):
            j = i + 1
            buf = []
            while j < n and not lines[j].startswith("```"):
                buf.append(lines[j])
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(buf)) +
                       "</code></pre>")
            i = j + 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = min(6, len(m.group(1)) + hshift)
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        if (line.lstrip().startswith("|") and i + 1 < n
                and _is_table_sep(lines[i + 1])):
            head = _cells(line)
            i += 2
            body = []
            while i < n and lines[i].lstrip().startswith("|"):
                body.append(_cells(lines[i]))
                i += 1
            t = ['<div class="scroll"><table>', "<tr>"]
            t += [f"<th>{_inline(c)}</th>" for c in head]
            t.append("</tr>")
            for r in body:
                t.append("<tr>" + "".join(
                    f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
            t.append("</table></div>")
            out.append("".join(t))
            continue

        m = re.match(r"^\s*([-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            ordered = not m.group(1)[0] in "-*+"
            items = []
            while i < n:
                mm = re.match(r"^\s*([-*+]|\d+[.)])\s+(.*)$", lines[i])
                if mm:
                    items.append(_inline(mm.group(2)))
                    i += 1
                elif (lines[i].startswith((" ", "\t")) and lines[i].strip()
                      and items):
                    items[-1] += " " + _inline(lines[i].strip())
                    i += 1
                else:
                    break
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{x}</li>" for x in items)
                       + f"</{tag}>")
            continue

        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].strip())
                i += 1
            out.append("<blockquote>" + _inline(" ".join(buf)) +
                       "</blockquote>")
            continue

        if not line.strip():
            i += 1
            continue

        buf = []
        while i < n and lines[i].strip() and not lines[i].startswith("```") \
                and not re.match(r"^#{1,6}\s", lines[i]) \
                and not lines[i].lstrip().startswith(("|", ">")) \
                and not re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>" + _inline(" ".join(buf)) + "</p>")
        else:
            i += 1
    return "\n".join(out)
