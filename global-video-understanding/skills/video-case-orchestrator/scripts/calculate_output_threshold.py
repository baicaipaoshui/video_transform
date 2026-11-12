#!/usr/bin/env python3
"""Calculate a first-write output threshold from real Case telemetry."""
import argparse
import json
import math
from pathlib import Path


def percentile95(values):
    if not values: raise ValueError("no first_write_token telemetry")
    ordered=sorted(values); rank=max(0,math.ceil(0.95*len(ordered))-1); return ordered[rank]


def main():
    parser=argparse.ArgumentParser(description="Compute P95(first_write_token) × 1.2")
    parser.add_argument("progress",nargs="+"); args=parser.parse_args(); values=[]
    for name in args.progress:
        data=json.loads(Path(name).read_text(encoding="utf-8"))
        values.extend(item.get("telemetry",{}).get("first_write_token") for item in data.get("sessions",[]) if isinstance(item.get("telemetry",{}).get("first_write_token"),(int,float)))
    p95=percentile95(values); print(json.dumps({"samples":len(values),"p95_first_write_token":p95,"threshold":math.ceil(p95*1.2)}))


if __name__=="__main__": main()
