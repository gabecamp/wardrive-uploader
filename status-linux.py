#!/usr/bin/env python3
"""Terminal status for a running Wardrive Uploader. Reads the same JSON the dashboard
polls, so it costs a few KB per refresh instead of a browser page. Ctrl-C to stop.

    python3 status-linux.py [--once] [--url http://127.0.0.1:8787]
"""
import argparse
import json
import os
import sys
import time
import urllib.request

CLEAR = "\033[H\033[2J"


def fetch(url):
    with urllib.request.urlopen(url + "/api/state", timeout=5) as r:
        return json.load(r)


def fmt_age(ts):
    if not ts:
        return "never"
    s = max(0, int(time.time() - ts))
    return f"{s // 60}m{s % 60:02d}s ago" if s >= 60 else f"{s}s ago"


def lines(s):
    w = s.get("wardrive", {})
    out = ["WARDRIVE UPLOADER  " + time.strftime("%H:%M:%S"),
           f"link {'ONLINE' if s else 'OFFLINE'}  uptime {int(s.get('uptime', 0)) // 60}m  "
           f"logging {'on' if w.get('logging') else 'off'}  {w.get('reason') or ''}",
           "", "WI-FI"]
    for a in w.get("adapters", []):
        out.append(f"  {a['name']:<22} {a['state']:<18} {a['networks']} networks")
    out.append(f"  in view {w.get('wifi', {}).get('in_view', 0)}  new {w.get('wifi', {}).get('new', 0)}")
    out += ["", "BLUETOOTH", f"  scan {w.get('bt', {}).get('state', '?')}  "
            f"in view {w.get('ble', {}).get('in_view', 0)}"]
    for o in w.get("boards", {}).get("others", []):
        out.append(f"  {o.get('name', '?'):<22} {o.get('kind', ''):<14} {o.get('transport', ''):<10} "
                   f"{o.get('state', '')}  {o.get('detail', '')}")
    out += ["", "MESH"]
    for d in s.get("devices", {}).get("mesh", {}).get("devices", []):
        err = d.get("error") or ""
        out.append(f"  {d['protocol']:<10} {d['transport']:<7} "
                   f"{'connected' if d.get('connected') else 'down'}  {err[:70]}")
    live = s.get("live", {})
    out += ["", f"LIVE  aircraft {live.get('aircraft', 0)}  mesh {live.get('mesh', 0)}  "
            f"pending mesh {s.get('pending', {}).get('mesh', 0)}",
            f"LAST CYCLE  {fmt_age((s.get('last_cycle') or {}).get('ts'))}  "
            f"errors: {', '.join((s.get('last_cycle') or {}).get('errors', [])) or 'none'}"]
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8787")
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    while True:
        try:
            body = "\n".join(lines(fetch(a.url)))
        except OSError as e:
            body = f"app not answering at {a.url} ({e})"
        if a.once:
            print(body)
            return
        sys.stdout.write(CLEAR + body + "\n")
        sys.stdout.flush()
        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        os._exit(0)
