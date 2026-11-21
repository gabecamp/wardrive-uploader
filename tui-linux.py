#!/usr/bin/env python3
"""Amber terminal UI for a running Wardrive Uploader. Reads the JSON the dashboard polls
and sends the same requests the dashboard does. Standard library, plus segno for the
Phone GPS QR code when it is installed.

    python3 tui-linux.py [--url http://127.0.0.1:8787]
    python3 tui-linux.py --dump        # status page as plain text

Keys everywhere: 1-9 pages · u upload now · s rescan · r refresh · x stop app (twice) · q quit
Boards page: a-i switch a device on/off.  Settings page: i interval · b start on boot ·
o upload on start · p Phone GPS port · c check update · I install update (twice).
Keys page: w WiGLE name+token · k WDGWars key · f wpa-sec key (typed, never shown).
"""
import argparse
import curses
import json
import time
import urllib.request

INTERVALS = [1, 4, 12, 24]  # config.INTERVAL_CHOICES_HOURS
PAGES = ["Status", "Feed", "Boards", "Uploads", "Leaders", "Captures", "Settings", "Keys", "Phone GPS"]

try:
    import segno
except ImportError:  # the QR is a nicety; the URL still works without it
    segno = None


def _req(url, path, body=None, timeout=8):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch(url):
    return _req(url, "/api/state")


def mins(s):
    s = int(max(0, s))
    return f"{s // 60}m{s % 60:02d}s"


def ago(ts):
    return mins(time.time() - ts) + " ago" if ts else "never"


def meter(rssi, width=28):
    f = max(0, min(width, round((rssi + 100) / 70 * width)))
    return "█" * f + "░" * (width - f)


def switchable(s):
    """Devices a letter key switches on the Boards page: (id, label)."""
    w = s.get("wardrive", {})
    out = [(a["id"], a["name"]) for a in w.get("adapters", []) if a.get("id")]
    out.append(("bt", "Bluetooth"))
    out += [(o["id"], f"{o.get('name', '?')} ({o.get('kind', '')})") for o in w.get("boards", {}).get("others", [])
            if o.get("id")]
    # A switched-off board drops out of the boards list, so keep its id or it can't be turned back on.
    seen = {d for d, _ in out}
    out += [(d, f"{d} (off)") for d in (s.get("config", {}).get("hw_off") or []) if d not in seen]
    return out[:9]


# ---- pages: each returns (text, style) rows; style is hi | amb | dim | rule ----

def page_status(s):
    w = s.get("wardrive", {})
    nets = sorted(w.get("feed_wifi", []), key=lambda n: -n["rssi"])
    ble = [b for b in w.get("feed_ble", []) if b.get("name")]
    mesh = (s.get("devices", {}).get("mesh", {}).get("devices") or [{}])[0]
    last = s.get("last_cycle") or {}
    live = s.get("live", {})
    rows = [("WI-FI RADIO", "hi")]
    for n in nets[:8]:
        rows.append((f"  {(n['ssid'] or '(hidden)')[:18]:<18} {meter(n['rssi'])}  {n['rssi']:>4} dBm  ch{n['channel']}", "amb"))
    rows.append((f"  {len(nets)} networks heard across {len(w.get('adapters', []))} adapter(s)", "dim"))
    rows.append(("", "amb"))
    rows.append((f"BLUETOOTH  {w.get('bt', {}).get('state', '?')} · {w.get('ble', {}).get('in_view', 0)} in view", "hi"))
    for b in ble[:5]:
        rows.append((f"  {b['name'][:18]:<18} {b['rssi']} dBm", "amb"))
    rows.append(("", "amb"))
    state = "connected" if mesh.get("connected") else "down"
    rows.append((f"MESH  {mesh.get('protocol', '-')} {state}  {(mesh.get('error') or '')[:48]}", "hi"))
    rows.append((f"LIVE  aircraft {live.get('aircraft', 0)}   mesh {live.get('mesh', 0)}"
                 f"   last cycle {ago(last.get('ts'))}", "amb"))
    rows.append((f"errors: {', '.join(last.get('errors', [])) or 'none'}", "dim"))
    rows.append((f"logging {'on' if w.get('logging') else 'off'} — {w.get('reason') or 'logs once there is a GPS fix'}", "dim"))
    return rows


def page_feed(s):
    f = s.get("feed", {})
    rows = [("AIRCRAFT", "hi")]
    for a in (f.get("aircraft") or [])[:6]:
        rows.append((f"  {str(a.get('flight') or a.get('hex') or '?').strip()[:10]:<10} alt {a.get('alt', '-')} "
                     f"{a.get('rssi', '')}", "amb"))
    if not f.get("aircraft"):
        rows.append(("  none", "dim"))
    rows.append(("", "amb"))
    rows.append(("MESH", "hi"))
    mesh = (f.get("meshtastic") or []) + (f.get("meshcore") or []) + (f.get("mesh") or [])
    for m in mesh[:8]:
        rows.append((f"  {str(m.get('name') or m.get('node_id') or '?')[:24]:<24} {m.get('snr', '')}", "amb"))
    if not mesh:
        rows.append(("  none", "dim"))
    rows.append(("", "amb"))
    rows.append(("WI-FI NOW", "hi"))
    for n in (s.get("wardrive", {}).get("feed_wifi") or [])[:6]:
        rows.append((f"  {(n['ssid'] or '(hidden)')[:24]:<24} {n['rssi']} dBm  ch{n['channel']}", "amb"))
    return rows


def page_boards(s):
    w = s.get("wardrive", {})
    b = w.get("boards", {})
    rows = [("BOARDS LINKED", "hi")]
    for o in b.get("others", []) + b.get("boards", []):
        rows.append((f"  {str(o.get('name', '?'))[:22]:<22} {str(o.get('kind', '')):<14} {o.get('transport', ''):<10} "
                     f"{o.get('state', '')}  {str(o.get('detail', ''))[:24]}", "amb"))
    if not (b.get("others") or b.get("boards")):
        rows.append(("  none linked", "dim"))
    rows.append(("", "amb"))
    rows.append(("NEARBY (not linked)", "hi"))
    for o in (b.get("nearby") or [])[:5]:
        rows.append((f"  {str(o.get('name', '?'))[:30]}", "amb"))
    rows.append(("", "amb"))
    off = set((s.get("config") or {}).get("hw_off") or [])
    rows.append(("SWITCHES  press the letter to toggle", "hi"))
    for i, (dev_id, label) in enumerate(switchable(s)):
        rows.append((f"  {'abcdefghi'[i]}  {label[:44]:<44} {'off' if dev_id in off else 'on'}", "amb"))
    return rows


def page_uploads(s, extra):
    lc = s.get("last_cycle") or {}
    rows = [("LAST UPLOAD CYCLE", "hi"),
            (f"  {ago(lc.get('ts'))}   aircraft sent {lc.get('aircraft_sent', 0)}   mesh sent {lc.get('mesh_sent', 0)}", "amb"),
            (f"  wigle {lc.get('wardrive', '-')}   adsb {lc.get('adsb', '-')}   mesh {lc.get('mesh', '-')}   wardrift {lc.get('wardrift', '-')}", "dim"),
            (f"  next run in {mins(max(0, s.get('next_run_ts', 0) - time.time()))}   every {s.get('interval_hours', '?')} h", "amb"),
            ("", "amb"),
            ("SESSION", "hi"),
            (f"  aircraft uploaded {s.get('session_uploaded', {}).get('aircraft', 0)}   "
             f"mesh uploaded {s.get('session_uploaded', {}).get('mesh', 0)}   "
             f"pending mesh {s.get('pending', {}).get('mesh', 0)}", "amb"),
            ("", "amb"),
            ("RECENT EVENTS", "hi")]
    for e in (s.get("events") or [])[:6]:  # the app keeps newest first
        if isinstance(e, dict):
            t = e.get("t")
            when = time.strftime("%H:%M:%S", time.localtime(t)) if isinstance(t, (int, float)) else "--:--:--"
            rows.append((f"  {when}  {str(e.get('msg', ''))[:80]}", "amb"))
        else:
            rows.append((f"  {str(e)[:90]}", "amb"))
    rows.append(("", "amb"))
    rows.append(("UPLOAD HISTORY (WiGLE)", "hi"))
    if extra.get("error"):
        rows.append((f"  {extra['error']}", "dim"))
    for u in (extra.get("list") or [])[:8]:
        rows.append((f"  {str(u)[:90]}", "amb"))
    return rows


def page_leaders(s, extra):
    rows = [("WIGLE TOP", "hi")]
    board = extra.get("wigle") or {}
    if not board.get("ok"):
        rows.append((f"  {board.get('error') or 'loading…'}", "dim"))
    for u in (board.get("users") or [])[:12]:
        mark = "*" if u.get("self") else " "
        rows.append((f"{mark} {u['rank']:>4}  {str(u['name'])[:20]:<20} wifi {u.get('wifi', 0):>10,}  "
                     f"bt {u.get('bt', 0):>9,}  month {u.get('month', 0):>8,}", "amb"))
    rows.append(("", "amb"))
    rows.append(("WARDRIFT", "hi"))
    wd = s.get("wardrift", {})
    if wd.get("error"):
        rows.append((f"  {wd['error']}", "dim"))
    for u in (wd.get("leaderboard") or [])[:6]:
        rows.append((f"  {u}", "amb"))
    return rows


def page_captures(s):
    c = s.get("wardrive", {}).get("captures", {})
    rows = [("CAPTURES", "hi"),
            (f"  pmkids {c.get('pmkids', 0)}   handshakes {c.get('handshakes', 0)}   "
             f"cracked {c.get('cracked', 0)}   sent to wpa-sec {c.get('sent', 0)}", "amb"),
            (f"  pending wpa-sec {c.get('pending_wpasec', 0)}   folder {c.get('folder', '')}", "dim"),
            (f"  key: {c.get('key_warning') or 'ok'}", "dim"),
            ("", "amb"),
            ("RECENT", "hi")]
    for r in (c.get("recent") or [])[:10]:
        rows.append((f"  {str(r)[:90]}", "amb"))
    if not c.get("recent"):
        rows.append(("  none yet", "dim"))
    return rows


def page_settings(s, msg):
    cfg = s.get("config", {})
    up = s.get("update", {})
    rows = [("SETTINGS", "hi"),
            (f"  i  upload interval      {cfg.get('interval_hours', '?')} h   (cycles 1 / 4 / 12 / 24)", "amb"),
            (f"  b  start on boot        {'on' if cfg.get('start_on_boot') else 'off'}", "amb"),
            (f"  o  upload on start      {'on' if cfg.get('upload_on_start') else 'off'}", "amb"),
            (f"  p  Phone GPS port       {cfg.get('gps_net_port', '?')}   (press p, type a number, Enter)", "amb"),
            ("", "amb"),
            ("UPDATES", "hi"),
            (f"  version {up.get('current', '?')}   latest {up.get('latest') or '-'}   "
             f"{'update available' if up.get('available') else 'up to date'}", "amb"),
            (f"  phase {up.get('phase', '-')} {up.get('progress', 0)}%   {up.get('error') or ''}", "dim"),
            ("  c  check now        I  install update (press twice)", "amb")]
    if msg:
        rows.append(("", "amb"))
        rows.append((msg, "hi"))
    return rows


def page_keys(s):
    cfg = s.get("config", {})
    yes = lambda v: "set" if v else "not set"
    return [("KEYS  (typed here, never shown back)", "hi"),
            (f"  k  WDGWars key        {yes(cfg.get('has_api_key'))}", "amb"),
            (f"  w  WiGLE name+token   {yes(cfg.get('has_wigle'))}   {cfg.get('wigle_api_name') or ''}", "amb"),
            (f"  f  wpa-sec key        {yes(cfg.get('has_wpasec'))}", "amb"),
            (f"  d  Wardrift upload key {yes(cfg.get('has_wardrift_key'))}", "amb"),
            ("", "amb"),
            ("Wardrift sign-in needs a password, so it stays on the dashboard.", "dim")]


def page_phone(s, extra):
    links = (extra.get("phone") or {}).get("links") or []
    rows = [("PHONE GPS", "hi")]
    if not links:
        rows.append(("  no address yet — is the app running on this network?", "dim"))
        return rows
    url = links[0].get("url", "")
    rows.append((f"  {url}", "amb"))
    rows.append(("  open it on the phone, accept the certificate warning once, allow location", "dim"))
    rows.append(("", "amb"))
    if segno is not None and url:
        m = segno.make(url, error="l", boost_error=False).matrix
        # Two matrix rows per text line using half blocks; a one-module quiet zone around it.
        size = len(m)
        pad = [[False] * (size + 2)]
        grid = pad + [[False] + list(r) + [False] for r in m] + pad
        for y in range(0, len(grid), 2):
            top, bot = grid[y], grid[y + 1] if y + 1 < len(grid) else [False] * len(grid[y])
            line = "".join("█" if t and b else "▀" if t else "▄" if b else " " for t, b in zip(top, bot))
            rows.append((line, "amb"))
    return rows


PAGE_FUNCS = {
    "Status": lambda s, x, m: page_status(s),
    "Feed": lambda s, x, m: page_feed(s),
    "Boards": lambda s, x, m: page_boards(s),
    "Uploads": lambda s, x, m: page_uploads(s, x.get("uploads", {})),
    "Leaders": lambda s, x, m: page_leaders(s, x),
    "Captures": lambda s, x, m: page_captures(s),
    "Settings": lambda s, x, m: page_settings(s, m),
    "Keys": lambda s, x, m: page_keys(s),
    "Phone GPS": lambda s, x, m: page_phone(s, x),
}


def fetch_extra(url, cache, now):
    """Pages that need their own request, refreshed at most every 60 s."""
    if now - cache.get("t", 0) > 60:
        try:
            cache["wigle"] = _req(url, "/api/wigle/board")
        except OSError as e:
            cache["wigle"] = {"ok": False, "error": f"WiGLE board unavailable ({e})"}
        try:
            cache["uploads"] = _req(url, "/api/uploads")
        except OSError as e:
            cache["uploads"] = {"error": f"uploads unavailable ({e})"}
        cache["t"] = now
    if "phone" not in cache or now - cache.get("tp", 0) > 300:
        try:
            cache["phone"] = _req(url, "/api/phone-gps")
        except OSError:
            cache["phone"] = {}
        cache["tp"] = now
    return cache


def draw(scr, page, rows, colours, footer):
    scr.erase()
    h, wd = scr.getmaxyx()
    tabs = "  ".join(f"[{i + 1}]{n}" + (" ●" if i == page else "") for i, n in enumerate(PAGES))
    head = ("═" * 78, "rule"), (" WARDRIVE  " + tabs, "hi"), ("═" * 78, "rule")
    body = list(head) + rows
    body = body[: h - 2] + [("═" * 78, "rule"), (footer, "dim")]
    for y, (text, style) in enumerate(body[: h]):
        try:
            scr.addnstr(y, 0, text, wd - 1, colours.get(style, 0))
        except curses.error:
            pass  # too narrow for this text: clip, don't crash
    scr.refresh()


def prompt(scr, label, secret, h, wd):
    """Read one line. Echo '*' for secrets. Enter confirms, Esc cancels (returns None)."""
    curses.echo(False)
    buf = []
    while True:
        shown = ("*" * len(buf)) if secret else "".join(buf)
        scr.addnstr(h - 1, 0, f"{label}: {shown}" + " " * max(0, wd - len(label) - len(shown) - 3), wd - 1)
        scr.move(h - 1, min(wd - 1, len(label) + 2 + len(shown)))
        scr.refresh()
        ch = scr.getch()
        if ch in (10, 13, curses.KEY_ENTER):
            return "".join(buf)
        if ch == 27:
            return None
        if ch in (127, 8, curses.KEY_BACKSPACE):
            if buf:
                buf.pop()
        elif 32 <= ch < 127:
            buf.append(chr(ch))


def act(url, page, s, key, scr, ctx):
    """Handle one key on the current page. Returns (notice, stop_app_now)."""
    h, wd = scr.getmaxyx()
    if ord("1") <= key <= ord("9") and key - ord("1") < len(PAGES):
        ctx["page"] = key - ord("1")
        return "", False
    if key == ord("u"):
        _req(url, "/api/upload", {})
        return "upload started", False
    if key == ord("s"):
        _req(url, "/api/rescan", {})
        return "rescanned", False
    name = PAGES[page]
    if name == "Boards" and ord("a") <= key <= ord("i"):
        devs = switchable(s)
        idx = key - ord("a")
        if idx < len(devs):
            dev_id, label = devs[idx]
            off = list(s.get("config", {}).get("hw_off") or [])
            off = [d for d in off if d != dev_id] if dev_id in off else off + [dev_id]
            _req(url, "/api/config", {"hw_off": off})
            return f"{label}: {'off' if dev_id in off else 'on'}", False
    if name == "Settings":
        cfg = s.get("config", {})
        if key == ord("i"):
            cur = cfg.get("interval_hours", INTERVALS[0])
            nxt = INTERVALS[(INTERVALS.index(cur) + 1) % len(INTERVALS)] if cur in INTERVALS else INTERVALS[0]
            _req(url, "/api/interval", {"interval_hours": nxt})
            return f"upload interval set to {nxt} h", False
        if key == ord("b"):
            v = not cfg.get("start_on_boot")
            _req(url, "/api/config", {"start_on_boot": v})
            return f"start on boot {'on' if v else 'off'}", False
        if key == ord("o"):
            v = not cfg.get("upload_on_start")
            _req(url, "/api/config", {"upload_on_start": v})
            return f"upload on start {'on' if v else 'off'}", False
        if key == ord("p"):
            typed = prompt(scr, "Phone GPS port", False, h, wd)
            if typed and typed.isdigit():
                _req(url, "/api/config", {"gps_net_port": int(typed)})
                return f"Phone GPS port set to {typed}", False
            return "port unchanged", False
        if key == ord("c"):
            res = _req(url, "/api/update/check", {})
            return ("update available" if res.get("available") else "up to date"), False
        if key == ord("I"):
            if ctx.get("install_armed"):
                ctx["install_armed"] = False
                _req(url, "/api/update/install", {})
                return "installing update", False
            ctx["install_armed"] = True
            return "press I again to install the update", False
    if name == "Keys":
        fields = {ord("k"): ("api_key", "WDGWars key", True),
                  ord("f"): ("wpasec_key", "wpa-sec key", True),
                  ord("d"): ("wardrift_api_key", "Wardrift upload key", True)}
        if key in fields:
            field, label, secret = fields[key]
            typed = prompt(scr, label, secret, h, wd)
            if typed:
                _req(url, "/api/config", {field: typed})
                return f"{label} saved", False
            return "nothing saved", False
        if key == ord("w"):
            name_ = prompt(scr, "WiGLE API name", False, h, wd)
            if name_:
                token = prompt(scr, "WiGLE API token", True, h, wd)
                if token:
                    _req(url, "/api/config", {"wigle_api_name": name_, "wigle_api_token": token})
                    return "WiGLE name and token saved", False
            return "nothing saved", False
    return "", False


def run(stdscr, url):
    curses.curs_set(0)
    stdscr.timeout(2000)
    colours = {"amb": 0, "hi": 0, "dim": 0, "rule": 0}
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        amber = 214 if curses.COLORS >= 256 else curses.COLOR_YELLOW
        teal = 37 if curses.COLORS >= 256 else curses.COLOR_CYAN
        curses.init_pair(1, amber, -1)
        curses.init_pair(2, teal, -1)
        curses.init_pair(3, 94 if curses.COLORS >= 256 else curses.COLOR_YELLOW, -1)
        curses.init_pair(4, 30 if curses.COLORS >= 256 else curses.COLOR_CYAN, -1)
        colours = {"amb": curses.color_pair(1), "hi": curses.color_pair(2) | curses.A_BOLD,
                   "dim": curses.color_pair(3), "rule": curses.color_pair(4)}
    ctx = {"page": 0, "install_armed": False}
    cache, notice, stop_armed, msg = {}, "", False, ""
    while True:
        page = PAGES[ctx["page"]]
        try:
            s = fetch(url)
            extra = fetch_extra(url, cache, time.time()) if page in ("Uploads", "Leaders", "Phone GPS") else cache
            rows = PAGE_FUNCS[page](s, extra, msg)
        except OSError as e:
            s, rows = {}, [(f"app not answering at {url} ({e})", "hi")]
        footer = notice or "1-9 pages · u upload · s rescan · r refresh · x stop app · q quit"
        draw(stdscr, ctx["page"], rows, colours, footer)
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key in (ord("x"), ord("X")):
            if stop_armed:
                try:
                    _req(url, "/api/quit", {})
                except OSError:
                    pass
                return
            stop_armed, notice = True, "press x again to stop the app"
            continue
        stop_armed = False
        if key == ord("r"):
            cache.pop("t", None)
            notice = "refreshed"
            continue
        if key == -1:
            continue
        if key != ord("I"):
            ctx["install_armed"] = False  # the update double-press only counts back-to-back
        try:
            notice, _ = act(url, ctx["page"], s, key, stdscr, ctx)
        except OSError as e:
            notice = f"request failed: {e}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8787")
    p.add_argument("--dump", action="store_true")
    a = p.parse_args()
    if a.dump:
        try:
            print("\n".join(text for text, _ in page_status(fetch(a.url))))
        except OSError as e:
            print(f"app not answering at {a.url} ({e})")
        return
    curses.wrapper(run, a.url)


if __name__ == "__main__":
    main()
