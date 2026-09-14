import sys
from pathlib import Path
CODE=Path("/Users/gamagarcia/Desktop/BUY-OR-WAIT/code"); sys.path.insert(0,str(CODE))
import loader
from finance.view import build_view
from decision.engine import decide
ds=loader.load()
for reqs,lbl in ((loader.sample_requests(),"25 samples"),(ds.requests,"250 requests")):
    na=0; na_con_fecha=0
    for req in reqs:
        v=build_view(ds,req,{})
        d=decide(v,req,ds.options_by_request.get(req.request_id,()))
        if d.affordability_status=="not_affordable":
            na+=1
            if v.earliest_full_payment is not None: na_con_fecha+=1
    print(f"{lbl}: not_affordable={na}, de los cuales con fecha de capacidad BORRADA por engine.py:75 = {na_con_fecha}")
