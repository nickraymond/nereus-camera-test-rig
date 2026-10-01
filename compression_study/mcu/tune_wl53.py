import sys, itertools, json
from pathlib import Path
from compression_study import mcu_codec_bench as b
from compression_study.common import run
import numpy as np
S=Path(sys.argv[1]); data=Path('/Users/nickbuemond/Documents/GitHub/nereus-camera-test-rig/data/s4_20260930')
raw, ctx = b.frameset(data, 'n6', 'cool', 'air')
ms=b.methods(S); base=ms['wl53']
wl=S/'wl53'/'wl53'
res=[]
for L,rnd,tilt in itertools.product((5,),(0.3,0.15),(0.3,0.22)):
    def enc(c,q,L=L,rnd=rnd,tilt=tilt):
        h,w=c.shape
        return run([str(wl),'enc',str(w),str(h),str(L),f'{q:.4f}',str(rnd),'1',str(tilt)],stdin=c.astype('<u2').tobytes())
    m=b.Method('t',base.fwd,base.inv,enc,base.dec,base.knob)
    rows=b.score_method('x',raw,ctx,m)
    s={t:b.at_target(rows,t,v) for t,v in b.TARGETS.items()}
    line=(L,rnd,tilt,*[round(s[t][k],3) for t in s for k in ('stress_de_mean','block_de_med','red_err_noise')])
    print(line, flush=True); res.append(line)
json.dump(res, open(S/'tune_wl53_b.json','w'))
