#!/usr/bin/env python3
"""Generate pipeline.json snapshot: weekly + monthly Booked/Held counts from Pipedrive.

Rule: Booked = deal entering stage Qualified (54).
      Held  = deal entering any stage from Demo Held onwards (79,158,463,292,291,187,188,159,277).
Attribution: by the date of the stage change itself.

Usage: PIPEDRIVE_TOKEN=... python3 gen_pipeline_snapshot.py [--fresh]
       (without --fresh, reuses /tmp/changelogs.json + /tmp/deals_*.json cache)
"""
import json, os, subprocess, sys, time, glob, datetime
from collections import defaultdict

TOKEN = os.environ.get("PIPEDRIVE_TOKEN")
if not TOKEN:
    sys.exit("Set PIPEDRIVE_TOKEN env var")

BOOKED_STAGE = "54"
HELD_STAGES = {"79","158","463","292","291","187","188","159","277"}
FILTER_ID = 248071

def api(path, out=None):
    out = out or path.replace("/", "_").replace("?", "_") + ".json"
    subprocess.run(["curl", "-s", "-m", "30",
                    f"https://api.pipedrive.com/v1/{path}{'&' if '?' in path else '?'}api_token={TOKEN}",
                    "-o", out], check=True)
    return json.load(open(out))

# 1) deals from the filter
deals = []
start = 0
while True:
    d = api(f"deals?filter_id={FILTER_ID}&limit=500&start={start}", f"/tmp/deals_{start}.json")
    deals += d["data"]
    if not d["additional_data"]["pagination"]["more_items_in_collection"]:
        break
    start += 500
    time.sleep(0.2)

booked_deals = [d for d in deals if str(d["stage_id"]) in HELD_STAGES | {BOOKED_STAGE}]
print("deals:", len(deals), "booked:", len(booked_deals))

# 2) per-deal changelogs
fresh = "--fresh" in sys.argv
if fresh or not os.path.exists("/tmp/changelogs.json"):
    logs = {}
    for i, d in enumerate(booked_deals):
        try:
            cl = api(f"deals/{d['id']}/changelog", f"/tmp/cl_{d['id']}.json")
            logs[str(d["id"])] = cl.get("data") or []
        except Exception as e:
            logs[str(d["id"])] = []
        if (i+1) % 100 == 0: print("changelog", i+1)
        time.sleep(0.1)
    json.dump(logs, open("/tmp/changelogs.json", "w"))
logs = json.load(open("/tmp/changelogs.json"))

def parse(ts):
    if not ts: return None
    try: return datetime.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    except: return None

def timeline(items):
    ev = []
    for it in reversed(items):
        fk = it.get("field_key"); ts = it.get("time") or it.get("log_time")
        if fk == "stage_id": ev.append(("stage", str(it.get("new_value")), ts))
        elif fk == "stage_change_time": ev.append(("time", str(it.get("new_value")), ts))
    out = []
    for i, (k, v, ts) in enumerate(ev):
        if k != "stage": continue
        t = parse(ts)
        if t is None:
            for j in range(i-1, -1, -1):
                if ev[j][0] == "time": t = parse(ev[j][2]); break
        if t is None:
            for j in range(i+1, len(ev)):
                if ev[j][0] == "time": t = parse(ev[j][2]); break
        out.append((v, t))
    return out

wb, wh = defaultdict(int), defaultdict(int)
mb, mh = defaultdict(int), defaultdict(int)
counted = 0
for did, items in logs.items():
    tl = timeline(items)
    q = next((t for s, t in tl if s == BOOKED_STAGE and t), None)
    h = next((t for s, t in tl if s in HELD_STAGES and t), None)
    if q is None:
        times = [parse(it.get("new_value")) for it in items if it.get("field_key") == "stage_change_time"]
        times = [t for t in times if t]
        if times: q = min(times)
    if q is None: continue
    counted += 1
    wb[(q - datetime.timedelta(days=(q.weekday()+1) % 7)).date().isoformat()] += 1
    mb[q.strftime("%Y-%m")] += 1
    if h:
        wh[(h - datetime.timedelta(days=(h.weekday()+1) % 7)).date().isoformat()] += 1
        mh[h.strftime("%Y-%m")] += 1

weeks = sorted(set(wb) | set(wh))
months = sorted(set(mb) | set(mh))
snapshot = {
    "generated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    "rule": "Booked = entered Qualified; Held = entered Demo Held or later (by stage-change date)",
    "weekly": [{"week_start": w, "booked": wb.get(w, 0), "held": wh.get(w, 0)} for w in weeks],
    "monthly": [{"month": m, "booked": mb.get(m, 0), "held": mh.get(m, 0)} for m in months],
    "totals": {"booked": sum(wb.values()), "held": sum(wh.values())},
}
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline.json")
json.dump(snapshot, open(out_path, "w"), indent=1)
print("counted booked:", counted, "| wrote", out_path)
