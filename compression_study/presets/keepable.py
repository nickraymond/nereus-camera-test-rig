"""Keepable sheets: one HTML that works as a self-contained download (images embedded) or as an
artifact with separate image files, plus a click-to-enlarge lightbox (Nick, 2026-10-05).

    sink = Sink(out_dir, embed=True)            # keepable single file
    sink = Sink(out_dir, embed=False)           # artifact: images written to out_dir/img/
    html = sink.img(array_or_path, "alt", inline_max=1400, lossless=True)
    page = "<style>…</style>" + LIGHTBOX + body

Each image is shown at a preview size inline and opens full resolution in the lightbox: wheel /
pinch zoom about the pointer up to 3200 %, drag to pan, Fit / 1:1 / 2× / 4× / 8× buttons,
double-click toggles 1:1 ↔ fit, Esc closes. Above 100 % pixels are drawn nearest-neighbour
(pixelated) so single pixels can be inspected.
"""

from __future__ import annotations

import base64
import html as _html
import io
from pathlib import Path

import numpy as np
from PIL import Image

LIGHTBOX = r'''<style>
img.zoomable{cursor:zoom-in}
#lb{position:fixed;inset:0;background:#0b0e10;z-index:50;display:grid;grid-template-rows:auto 1fr}
#lb[hidden]{display:none!important}
#lb .bar{display:flex;gap:6px;align-items:center;padding:8px 12px;padding-top:calc(8px + env(safe-area-inset-top,0px));color:#e6ecf0;font:13px system-ui,sans-serif;flex-wrap:wrap}
#lb .bar button{background:#1f2a31;color:#e6ecf0;border:1px solid #33424c;border-radius:4px;padding:4px 10px;cursor:pointer;font:inherit}
#lb .bar .t{flex:1 1 200px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#lb .stage{position:relative;overflow:hidden;cursor:grab;touch-action:none}
#lb .stage.drag{cursor:grabbing}
#lb #lbi{position:absolute;left:0;top:0;width:auto!important;height:auto!important;max-width:none!important;max-height:none!important;border-radius:0!important;transform-origin:0 0;user-select:none;-webkit-user-drag:none}
</style>
<div id="lb" hidden role="dialog" aria-label="Image viewer"><div class="bar"><span class="t" id="lbt"></span>
<button data-z="fit">Fit</button><button data-z="1">1:1</button><button data-z="2">2×</button><button data-z="4">4×</button><button data-z="8">8×</button>
<span id="lbz" style="min-width:4em;text-align:right"></span><button id="lbx">Close (Esc)</button></div>
<div class="stage" id="lbs"><img id="lbi" alt=""></div></div>
<script>
(function(){
  const lb=document.getElementById('lb'),img=document.getElementById('lbi'),stage=document.getElementById('lbs'),
        zt=document.getElementById('lbz'),tt=document.getElementById('lbt');
  let s=1,x=0,y=0,fit=1;const ptr=new Map();let last=null;
  function apply(){img.style.transform='translate('+x+'px,'+y+'px) scale('+s+')';
    img.style.imageRendering=s>1.0001?'pixelated':'auto';zt.textContent=Math.round(s*100)+' %';}
  function fitView(){const r=stage.getBoundingClientRect();if(!img.naturalWidth)return;
    fit=Math.min(r.width/img.naturalWidth,r.height/img.naturalHeight,1);s=fit;
    x=(r.width-img.naturalWidth*s)/2;y=(r.height-img.naturalHeight*s)/2;apply();}
  function zoomAt(ns,cx,cy){ns=Math.max(Math.min(fit,1)*0.5,Math.min(ns,32));x=cx-(cx-x)*ns/s;y=cy-(cy-y)*ns/s;s=ns;apply();}
  function centre(){const r=stage.getBoundingClientRect();return[r.width/2,r.height/2];}
  function open(src,title){tt.textContent=title||'';lb.hidden=false;img.onload=fitView;img.src=src;if(img.complete&&img.naturalWidth)fitView();}
  function close(){lb.hidden=true;img.removeAttribute('src');}
  document.addEventListener('click',e=>{const t=e.target.closest&&e.target.closest('img.zoomable');
    if(t){e.preventDefault();open(t.dataset.full||t.currentSrc||t.src,t.alt);}});
  document.addEventListener('keydown',e=>{const t=e.target.closest&&e.target.closest('img.zoomable');
    if(lb.hidden){if(t&&(e.key==='Enter'||e.key===' ')){e.preventDefault();open(t.dataset.full||t.src,t.alt);}return;}
    if(e.key==='Escape')close();const st=60;
    if(e.key==='ArrowLeft'){x+=st;apply();}if(e.key==='ArrowRight'){x-=st;apply();}
    if(e.key==='ArrowUp'){y+=st;apply();}if(e.key==='ArrowDown'){y-=st;apply();}
    if(e.key==='+'||e.key==='='){const c=centre();zoomAt(s*1.25,c[0],c[1]);}if(e.key==='-'){const c=centre();zoomAt(s/1.25,c[0],c[1]);}});
  document.getElementById('lbx').onclick=close;
  lb.querySelectorAll('[data-z]').forEach(b=>b.onclick=()=>{const z=b.dataset.z;if(z==='fit')return fitView();
    const c=centre();zoomAt(parseFloat(z),c[0],c[1]);});
  stage.addEventListener('wheel',e=>{e.preventDefault();const r=stage.getBoundingClientRect();
    zoomAt(s*Math.exp(-e.deltaY*0.0015),e.clientX-r.left,e.clientY-r.top);},{passive:false});
  stage.addEventListener('dblclick',e=>{const r=stage.getBoundingClientRect();
    if(Math.abs(s-1)<1e-3)fitView();else zoomAt(1,e.clientX-r.left,e.clientY-r.top);});
  stage.addEventListener('pointerdown',e=>{stage.setPointerCapture(e.pointerId);ptr.set(e.pointerId,[e.clientX,e.clientY]);stage.classList.add('drag');last=null;});
  stage.addEventListener('pointermove',e=>{if(!ptr.has(e.pointerId))return;const prev=ptr.get(e.pointerId);ptr.set(e.pointerId,[e.clientX,e.clientY]);
    if(ptr.size===1){x+=e.clientX-prev[0];y+=e.clientY-prev[1];apply();}
    else if(ptr.size===2){const p=[...ptr.values()];const d=Math.hypot(p[0][0]-p[1][0],p[0][1]-p[1][1]);
      const r=stage.getBoundingClientRect();const cx=(p[0][0]+p[1][0])/2-r.left,cy=(p[0][1]+p[1][1])/2-r.top;
      if(last)zoomAt(s*d/last,cx,cy);last=d;}});
  function up(e){ptr.delete(e.pointerId);if(ptr.size<2)last=null;if(!ptr.size)stage.classList.remove('drag');}
  stage.addEventListener('pointerup',up);stage.addEventListener('pointercancel',up);
  window.addEventListener('resize',()=>{if(!lb.hidden)fitView();});
})();
</script>'''


def _encode(im: Image.Image, fmt: str, lossless: bool, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    if fmt == "WEBP":
        im.save(buf, "WEBP", lossless=lossless, quality=100 if lossless else quality, method=4)
    elif fmt == "PNG":
        im.save(buf, "PNG", optimize=True)
    else:
        im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


class Sink:
    """Writes images either embedded (data URIs) or as files under ``out_dir/img``."""

    def __init__(self, out_dir: Path, embed: bool):
        self.out_dir, self.embed, self.n, self.files = Path(out_dir), embed, 0, {}
        self.bytes = 0
        if not embed:
            (self.out_dir / "img").mkdir(parents=True, exist_ok=True)

    def _url(self, data: bytes, ext: str) -> str:
        self.n += 1
        self.bytes += len(data)
        if self.embed:
            mime = {"webp": "image/webp", "png": "image/png", "jpg": "image/jpeg"}[ext]
            return f"data:{mime};base64," + base64.b64encode(data).decode()
        rel = f"img/i{self.n:03d}.{ext}"
        (self.out_dir / rel).write_bytes(data)
        self.files[rel] = str(self.out_dir / rel)
        return rel

    def img(self, src, alt: str, inline_max: int = 1400, lossless: bool = True,
            quality: int = 90, pixelated_inline: bool = False, css: str = "") -> str:
        """``src``: a path or an HxWx3 uint8 array. Full resolution goes to the lightbox (lossless
        WebP by default, or lossy WebP ``quality``); a JPEG preview is used inline when wider than
        ``inline_max``."""
        im = Image.open(src).convert("RGB") if not isinstance(src, np.ndarray) else \
            Image.fromarray(src)
        full = self._url(_encode(im, "WEBP", lossless, quality), "webp")
        if im.width > inline_max:
            pv = im.resize((inline_max, round(inline_max * im.height / im.width)),
                           Image.Resampling.LANCZOS)
            inline = self._url(_encode(pv, "JPEG", False, 88), "jpg")
        else:
            inline = full
        style = ("image-rendering:pixelated;" if pixelated_inline else "") + css
        full_attr = "" if inline is full else f' data-full="{full}"'   # no duplicate data URI
        return (f'<img class="zoomable" tabindex="0" src="{inline}"{full_attr} '
                f'alt="{_html.escape(alt)}" title="{_html.escape(alt)} — click to enlarge '
                f'({im.width}×{im.height})"' + (f' style="{style}"' if style else "") + '>')
