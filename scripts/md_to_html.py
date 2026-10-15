#!/usr/bin/env python3
"""리포트 마크다운을 메일용 HTML로 변환 (표, 제목, 목록, 굵게만 지원).

사용: python3 -I scripts/md_to_html.py report.md > report.html
"""
import html, re, sys

# 메일 서비스가 <style>을 지우는 경우가 있어 모든 스타일을 태그에 직접 넣음
TABLE = ('<table border="1" cellpadding="5" cellspacing="0" '
         'style="border-collapse:collapse;border:1px solid #999;font-size:13px;margin:8px 0">')
TH = '<th bgcolor="#eef1f5" nowrap style="border:1px solid #999">'
TD = '<td style="border:1px solid #999">'
TDR = '<td align="right" nowrap style="border:1px solid #999">'


def inline(t):
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"~~(.+?)~~", r"<s>\1</s>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def convert(md):
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            align = ["right" if c.endswith(":") else "left" for c in cells(lines[i + 1])]
            out.append(TABLE)
            out.append("<tr>" + "".join(f"{TH}{inline(c)}</th>" for c in cells(ln)) + "</tr>")
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                row = cells(lines[i])
                out.append("<tr>" + "".join(
                    (f"{TDR}{inline(c)}</td>" if k < len(align) and align[k] == "right"
                     else f"{TD}{inline(c)}</td>")
                    for k, c in enumerate(row)) + "</tr>")
                i += 1
            out.append("</table>")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif re.match(r"^\s*([-*]|\d+\.)\s+", ln):
            tag = "ol" if re.match(r"^\s*\d+\.", ln) else "ul"
            out.append(f"<{tag}>")
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                out.append("<li>" + inline(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i])) + "</li>")
                i += 1
            out.append(f"</{tag}>")
            continue
        elif ln.strip():
            out.append(f"<p>{inline(ln)}</p>")
        i += 1
    return ('<div style="font-family:Apple SD Gothic Neo,Malgun Gothic,sans-serif;'
            'font-size:14px;color:#1f2328;line-height:1.5">' + "\n".join(out) + "</div>")


if __name__ == "__main__":
    print(convert(open(sys.argv[1], encoding="utf-8").read()))
