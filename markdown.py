# -*- coding: utf-8 -*-
"""
記事用の最小マークダウン変換。

汎用パーサではなく、このサイトの記事で実際に使う記法だけを扱う:
見出し / 段落 / 太字 / リンク / 箇条書き / 番号付き / チェックリスト /
表 / 引用 / 区切り線。

引用(>)は「ここだけ覚えて帰ってほしい一文」に使われるので、
売場の手書きPOPとして描く。
チェックリストは買う直前に使う道具なので、実際に押せるようにする。
"""

import html
import re

INLINE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _inline(text):
    t = html.escape(text, quote=False)
    t = INLINE_LINK.sub(lambda m: '<a href="%s">%s</a>' % (
        html.escape(m.group(2), quote=True), m.group(1)), t)
    t = BOLD.sub(r"<strong>\1</strong>", t)
    return t


def _table(rows):
    """|---|---:| の行で右寄せを判定する（金額の列があるため）"""
    if len(rows) < 2:
        return ""
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    align_row = [c.strip() for c in rows[1].strip("|").split("|")]
    aligns = ["right" if c.endswith(":") else "left" for c in align_row]

    def cells(row, tag):
        out = ""
        for i, c in enumerate(row.strip("|").split("|")):
            a = aligns[i] if i < len(aligns) else "left"
            style = ' class="ta-right"' if a == "right" else ""
            out += "<%s%s>%s</%s>" % (tag, style, _inline(c.strip()), tag)
        return "<tr>%s</tr>" % out

    body = "".join(cells(r, "td") for r in rows[2:])
    return ('<div class="table-wrap"><table class="guide-table">'
            '<thead>%s</thead><tbody>%s</tbody></table></div>'
            % (cells(rows[0], "th"), body))


CTA = re.compile(r"^\{\{cta:(\w+)\}\}$")


def render(md, heading_offset=1, cta_renderer=None):
    """マークダウンをHTMLへ。見出しは heading_offset だけ深くする
    （ページのh1と重複させないため # → h2）。"""
    lines = md.split("\n")
    out, i = [], 0
    toc = []
    check_index = [0]

    def close(stack):
        while stack:
            out.append("</%s>" % stack.pop())

    stack = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 記事に差し込むリンク。登録簿にURLが無ければ何も出さない。
        m = CTA.match(stripped)
        if m:
            close(stack)
            if cta_renderer:
                out.append(cta_renderer(m.group(1)))
            i += 1
            continue

        # 表
        if stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            close(stack)
            out.append(_table(rows))
            continue

        # チェックリスト（買う直前に使う道具なので押せるようにする）
        if re.match(r"^- \[[ xX]\] ", stripped):
            close(stack)
            items = []
            while i < len(lines) and re.match(r"^- \[[ xX]\] ", lines[i].strip()):
                text = lines[i].strip()[6:]
                items.append(
                    '<li><label class="check">'
                    '<input type="checkbox" data-check="%d"><span>%s</span>'
                    '</label></li>' % (check_index[0], _inline(text)))
                check_index[0] += 1
                i += 1
            out.append('<ul class="checklist" data-checklist>%s</ul>'
                       '<button type="button" class="check-reset" data-check-reset>'
                       'チェックを全部外す</button>' % "".join(items))
            continue

        # 区切り線
        if stripped in ("---", "***", "___"):
            close(stack)
            out.append('<hr class="guide-rule">')
            i += 1
            continue

        # 見出し
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            close(stack)
            level = min(len(m.group(1)) + heading_offset, 6)
            text = m.group(2).strip()
            anchor = "s%d" % len(toc)
            if level == 2:
                toc.append((anchor, text))
                out.append('<h2 id="%s" class="guide-h2">%s</h2>' % (anchor, _inline(text)))
            else:
                out.append('<h%d class="guide-h%d">%s</h%d>' % (level, level, _inline(text), level))
            i += 1
            continue

        # 引用 = ここだけ覚えて帰ってほしい一文
        if stripped.startswith(">"):
            close(stack)
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip("> ").strip())
                i += 1
            out.append('<blockquote class="pop-quote">%s</blockquote>'
                       % _inline(" ".join(b for b in buf if b)))
            continue

        # 箇条書き / 番号付き
        m = re.match(r"^(-|\d+\.)\s+(.*)$", stripped)
        if m:
            tag = "ul" if m.group(1) == "-" else "ol"
            if not stack or stack[-1] != tag:
                close(stack)
                out.append("<%s>" % tag)
                stack.append(tag)
            out.append("<li>%s</li>" % _inline(m.group(2)))
            i += 1
            continue

        # 空行
        if not stripped:
            close(stack)
            i += 1
            continue

        # 段落
        close(stack)
        out.append("<p>%s</p>" % _inline(stripped))
        i += 1

    close(stack)
    return "".join(out), toc


def parse_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    end = text.index("\n---", 3)
    meta = {}
    for line in text[3:end].strip().split("\n"):
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, text[end + 4:].lstrip("\n")
