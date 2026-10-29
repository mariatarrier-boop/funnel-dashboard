#!/usr/bin/env python3
"""Generate pipeline.json for the dashboard - Pipedrive Booked/Held per week & month.

Method (matches Ria's manual counting):
  Filter: pipeline 12, company in 576/578/580, lost_reason != Duplicate.
  Booked = deals NOT in Unqualified (52) or Positive Contact Made (55) - current stage.
  Held   = deals NOT in Unqualified/PCM/Qualified (54) - current stage.
  Bucketed by deal CREATED date. Week groups: Sun+Mon, Tue, Wed, Thu, Fri+Sat.
"""
import json, os, subprocess, sys, time, datetime
from collections import defaultdict

TOKEN = os.environ.get("PIPEDRIVE_TOKEN")
if not TOKEN:
    sys.exit("Set PIPEDRIVE_TOKEN env var")

def api(path, out):
    subprocess.run(["curl", "-s", "-m", "30",
                    f"https://api.pipedrive.com/v1/{path}{'&' if '?' in path else '?'}api_token={TOKEN}",
                    "-o", out], check=True)
    return json.load(open(out))

# 1) temp filter for Aug 30 -> today
today = datetime.date.today().isoformat()
f = api("", "/tmp/gen_filter.json")  # placeholder no-op
import urllib.request
def post(url, payload, out):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        json.dump(json.load(r), open(out, "w"))
post(f"https://api.pipedrive.com/v1/filters?api_token={TOKEN}", {
    "name": "TEMP - dashboard gen (auto-delete)",
    "type": "deals",
    "temporary_flag": True,
    "conditions": {"glue": "and", "conditions": [
        {"glue": "and", "conditions": [
            {"object": "deal", "field_id": "12450", "operator": ">=", "value": "2026-08-30", "json_value_flag": False},
            {"object": "deal", "field_id": "12450", "operator": "<=", "value": today, "json_value_flag": False},
            {"object": "deal", "field_id": "12458", "operator": "!=", "value": "Duplicate", "json_value_flag": False},
            {"object": "deal", "field_id": "12446", "operator": "=", "value": "12", "json_value_flag": False},
            {"object": "deal", "field_id": "12604", "operator": "=", "value": "576,580,578", "json_value_flag": False},
        ]},
        {"glue": "or", "conditions": []},
    ]},
}, "/tmp/gen_filter.json")
fid = json.load(open("/tmp/gen_filter.json"))["data"]["id"]

# 2) page deals
deals = []
start = 0
while True:
    d = api(f"deals?filter_id={fid}&limit=500&start={start}", f"/tmp/gen_deals_{start}.json")
    deals += d["data"]
    if not d["additional_data"]["pagination"]["more_items_in_collection"]:
        break
    start += 500
    time.sleep(0.2)
subprocess.run(["curl", "-s", "-X", "DELETE",
                f"https://api.pipedrive.com/v1/filters/{fid}?api_token={TOKEN}"],
               capture_output=True)

# 3) bucket
def week_key(dstr):
    dt = datetime.date.fromisoformat(dstr)
    return dt - datetime.timedelta(days=(dt.weekday() + 1) % 7)

wb = defaultdict(lambda: [0]*5); wh = defaultdict(lambda: [0]*5)
mb = defaultdict(int); mh = defaultdict(int)
for d in deals:
    day = datetime.date.fromisoformat(d["add_time"][:10])
    w = week_key(d["add_time"][:10])
    col = {0:0,1:0,2:1,3:2,4:3,5:4,6:4}[(day.weekday()+1) % 7]
    if d["stage_id"] not in (52, 55):
        wb[w][col] += 1
        mb[w.strftime("%Y-%m")] += 1
        if d["stage_id"] not in (52, 55, 54):
            wh[w][col] += 1
            mh[w.strftime("%Y-%m")] += 1

weeks = sorted(wb)
snapshot = {
    "generated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    "rule": "Booked = deal not in Unqualified/Positive Contact Made (current stage); Held = also not in Qualified. Bucketed by deal created date; week groups: Sun+Mon, Tue, Wed, Thu, Fri+Sat.",
    "weekly": [{
        "week_start": w.isoformat(),
        "groups": ["Sun+Mon", "Tue", "Wed", "Thu", "Fri+Sat"],
        "booked": wb[w], "held": wh[w],
        "booked_total": sum(wb[w]), "held_total": sum(wh[w]),
    } for w in weeks],
    "monthly": [{"month": m, "booked": mb[m], "held": mh[m]} for m in sorted(mb)],
    "totals": {"booked": sum(mb.values()), "held": sum(mh.values())},
}
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline.json")
json.dump(snapshot, open(out_path, "w"), indent=1)
print("wrote", out_path, "| weeks:", len(weeks), "| totals:", snapshot["totals"])
