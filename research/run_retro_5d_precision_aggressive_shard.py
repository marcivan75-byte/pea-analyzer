from __future__ import annotations
import os
from research import retro_5d_precision_search_aggressive as a

shard=int(os.environ.get('SHARD','0'))
parts=3
features=list(a.FEATURES)
chunk=(len(features)+parts-1)//parts
start=shard*chunk
end=min(len(features),(shard+1)*chunk)
a.FEATURES=features[start:end]
a.BEAM=120
a.MAX_DEPTH=5
a.MIN_DISC=50
a.MIN_WINS=5

if not a.FEATURES:
    raise SystemExit(f'EMPTY_SHARD_{shard}')

if __name__=='__main__':
    print({'shard':shard,'features':a.FEATURES,'count':len(a.FEATURES)})
    a.main()
