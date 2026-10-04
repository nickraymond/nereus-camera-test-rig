"""Shrink a single-file before-after-report page: embed each distinct image once.

    python -m compression_study.presets.dedupe_inline in.html out.html

build_report.py (without --split) writes a data: URI for every attribute that shows an image,
so an image used as both the inline view and the full-screen view, or as the RAW reference of
several sections, is embedded several times. This moves each distinct data: URI into one JS
array and replaces every occurrence with a tiny placeholder (``data:,#N``, a valid empty data
URI, so nothing is fetched); a script placed before the viewer's own script puts the real URI
back into every attribute when the page loads. Google Fonts <link> tags are removed so the file
has no external links at all (the page falls back to system fonts). Same pixels.
"""

import re
import sys

URI = re.compile(r"data:image/[a-z+]+;base64,[A-Za-z0-9+/=]+")
RESOLVE = ("<script>(function(){var I=__IMGS__;var all=document.querySelectorAll('*');"
           "for(var i=0;i<all.length;i++){var el=all[i];for(var j=0;j<el.attributes.length;j++){"
           "var a=el.attributes[j];var m=/^data:,#(\\d+)$/.exec(a.value);"
           "if(m){el.setAttribute(a.name,I[+m[1]]);}}}})();</script>\n")


def main(src: str, dst: str) -> int:
    s = open(src, encoding="utf-8").read()
    s = re.sub(r"<link[^>]*fonts\.(googleapis|gstatic)\.com[^>]*>\s*", "", s)
    index: dict[str, int] = {}
    s2 = URI.sub(lambda m: f"data:,#{index.setdefault(m.group(0), len(index))}", s)
    arr = "[" + ",".join(f'"{u}"' for u in index) + "]"
    k = s2.rfind("<script")
    if k < 0:
        raise SystemExit("no <script> in the page")
    out = s2[:k] + RESOLVE.replace("__IMGS__", arr) + s2[k:]
    open(dst, "w", encoding="utf-8").write(out)
    print(f"{src}: {len(s) / 1e6:.1f} MB → {dst}: {len(out) / 1e6:.1f} MB "
          f"({len(index)} distinct images)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
