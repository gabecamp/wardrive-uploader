"""Wi-Fi scanning on Linux through nl80211, on every managed Wi-Fi interface.

Same contract as the Windows scanner in :mod:`.wifi`: each pass asks every
adapter to scan, then reads its BSS list and returns only entries heard within
the last few seconds. Talks generic netlink directly, so the raw information
elements reach :func:`.wifi.auth_mode` unchanged and no extra package is needed.

Triggering a scan needs root or CAP_NET_ADMIN. Without it the kernel's cached
list (filled by whatever else scans, e.g. wpa_supplicant) is still read.
"""

from __future__ import annotations

import os
import socket
import struct
import time
from pathlib import Path
from typing import Any

from .wifi import FRESH_SECONDS, auth_mode, channel

_GENL_CTRL, _CTRL_GETFAMILY, _CTRL_FAMILY_ID, _CTRL_FAMILY_NAME = 0x10, 3, 1, 2
_REQUEST, _ACK, _DUMP = 0x1, 0x4, 0x300
_NLMSG_ERROR, _NLMSG_DONE = 2, 3
_GET_INTERFACE, _GET_SCAN, _TRIGGER_SCAN = 5, 32, 33
_A_IFINDEX, _A_IFNAME, _A_IFTYPE, _A_SCAN_SSIDS, _A_BSS = 3, 4, 5, 45, 47
_B_BSSID, _B_FREQ, _B_CAP, _B_IES, _B_SIGNAL_MBM, _B_SEEN_MS, _B_BEACON_IES = 1, 2, 5, 6, 7, 10, 11
_IFTYPE_STATION = 2


def _attr(kind: int, payload: bytes) -> bytes:
    return struct.pack("HH", 4 + len(payload), kind) + payload + b"\0" * (-len(payload) % 4)


def _attrs(buf: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    i = 0
    while i + 4 <= len(buf):
        ln, kind = struct.unpack_from("HH", buf, i)
        if ln < 4:
            break
        out[kind & 0x3FFF] = buf[i + 4:i + ln]
        i += (ln + 3) & ~3
    return out


def _ssid(ies: bytes) -> str:
    i = 0
    while i + 2 <= len(ies):
        eid, ln = ies[i], ies[i + 1]
        if eid == 0:
            raw = ies[i + 2:i + 2 + ln]
            return raw.decode("utf-8", "replace") if any(raw) else ""
        i += 2 + ln
    return ""


def _describe(ifname: str) -> str:
    """``wlan0 (brcmfmac)``: the name the dashboard shows and keys its switch on."""
    try:
        return f"{ifname} ({Path(os.readlink(f'/sys/class/net/{ifname}/device/driver')).name})"
    except OSError:
        return ifname


def _state(ifname: str) -> str:
    try:
        up = Path(f"/sys/class/net/{ifname}/operstate").read_text().strip() == "up"
    except OSError:
        return "not ready"
    return "connected" if up else "disconnected"


class WifiScanner:
    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 16)   # NETLINK_GENERIC
        self.sock.bind((0, 0))
        self.sock.settimeout(5)
        self.seq = 0
        self.family = _GENL_CTRL
        try:
            reply = self._request(_CTRL_GETFAMILY, _ACK, _attr(_CTRL_FAMILY_NAME, b"nl80211\0"))
            self.family = struct.unpack("H", reply[0][_CTRL_FAMILY_ID][:2])[0]
        except (OSError, IndexError, KeyError) as e:
            self.sock.close()
            raise OSError(f"Wi-Fi service unavailable ({e})") from e
        self.adapters: list[dict[str, Any]] = []
        self.off: set[str] = set()       # adapter names switched off
        self._denied: set[str] = set()   # adapters this process may not ask to scan

    def close(self) -> None:
        self.sock.close()

    def _request(self, cmd: int, flags: int, attrs: bytes = b"") -> list[dict[int, bytes]]:
        self.seq += 1
        body = struct.pack("BBH", cmd, 0, 0) + attrs
        self.sock.send(struct.pack("IHHII", 16 + len(body), self.family, flags | _REQUEST, self.seq, 0) + body)
        out: list[dict[int, bytes]] = []
        while True:
            data = self.sock.recv(1 << 17)
            i = 0
            while i + 16 <= len(data):
                ln, kind, _, seq, _ = struct.unpack_from("IHHII", data, i)
                payload = data[i + 16:i + ln]
                i += (ln + 3) & ~3
                if seq != self.seq:
                    continue                      # left over from an earlier, timed-out request
                if kind == _NLMSG_DONE:
                    return out
                if kind == _NLMSG_ERROR:
                    err = -struct.unpack_from("i", payload)[0]
                    if err:
                        raise OSError(err, os.strerror(err))
                    return out
                out.append(_attrs(payload[4:]))

    def _interfaces(self) -> list[tuple[int, str]]:
        """(ifindex, description) of every managed (station) interface."""
        out = []
        try:
            msgs = self._request(_GET_INTERFACE, _DUMP)
        except OSError:
            return []
        for m in msgs:
            if _A_IFINDEX not in m or _A_IFNAME not in m:
                continue                          # e.g. a P2P device, which has no netdev
            if struct.unpack("I", m.get(_A_IFTYPE, b"\0\0\0\0")[:4])[0] != _IFTYPE_STATION:
                continue
            ifname = m[_A_IFNAME].split(b"\0", 1)[0].decode()
            out.append((struct.unpack("I", m[_A_IFINDEX][:4])[0], ifname))
        return out

    def trigger(self) -> list[tuple[int, str]]:
        """Ask every adapter to scan. Returns the adapters."""
        ifs = self._interfaces()
        for idx, ifname in ifs:
            name = _describe(ifname)
            if name in self.off:
                continue
            # One empty SSID makes it an active scan (probe requests), like Windows does.
            req = _attr(_A_IFINDEX, struct.pack("I", idx)) + _attr(_A_SCAN_SSIDS, _attr(1, b""))
            try:
                self._request(_TRIGGER_SCAN, _ACK, req)
                self._denied.discard(name)
            except PermissionError:
                self._denied.add(name)
            except OSError:
                pass                              # busy with a scan already, or the interface is down
        return ifs

    def read(self, ifs: list[tuple[int, str]]) -> list[dict[str, Any]]:
        """Fresh networks across all adapters; the strongest reading wins when
        two adapters hear the same network."""
        best: dict[str, dict[str, Any]] = {}
        now_mono, now_utc = time.monotonic(), time.time()
        adapters = []
        for idx, ifname in ifs:
            name = _describe(ifname)
            if name in self.off:
                adapters.append({"name": name, "state": "off", "networks": 0})
                continue
            state = "needs root to scan" if name in self._denied else _state(ifname)
            try:
                msgs = self._request(_GET_SCAN, _DUMP, _attr(_A_IFINDEX, struct.pack("I", idx)))
            except OSError:
                adapters.append({"name": name, "state": state, "networks": 0})
                continue
            seen = 0
            for m in msgs:
                b = _attrs(m.get(_A_BSS, b""))
                if _B_BSSID not in b or _B_FREQ not in b:
                    continue
                age = struct.unpack("I", b[_B_SEEN_MS][:4])[0] / 1000.0 if _B_SEEN_MS in b else 0.0
                if age > FRESH_SECONDS:
                    continue
                mac = b[_B_BSSID][:6].hex(":")
                if mac == "00:00:00:00:00:00":
                    continue
                seen += 1
                rssi = struct.unpack("i", b[_B_SIGNAL_MBM][:4])[0] // 100 if _B_SIGNAL_MBM in b else -100
                if mac in best and best[mac]["rssi"] >= rssi:
                    continue
                ies = b.get(_B_IES) or b.get(_B_BEACON_IES) or b""
                freq = struct.unpack("I", b[_B_FREQ][:4])[0]
                cap = struct.unpack("H", b[_B_CAP][:2])[0] if _B_CAP in b else 0
                best[mac] = {
                    "mac": mac, "ssid": _ssid(ies), "rssi": rssi, "freq": freq, "channel": channel(freq),
                    "auth": auth_mode(ies, cap), "adapter": name,
                    "at": now_mono - age, "utc": now_utc - age,   # when it was actually heard
                }
            adapters.append({"name": name, "state": state, "networks": seen})
        self.adapters = adapters
        return list(best.values())


if __name__ == "__main__":  # manual check: python -m wdgwars_uploader.wardrive.wifi_linux
    s = WifiScanner()
    found = s.trigger()
    assert found, "no managed Wi-Fi interface"
    time.sleep(5)
    nets = s.read(found)
    for n in sorted(nets, key=lambda r: -r["rssi"]):
        print(f"{n['mac']}  {n['rssi']:4d}  ch{n['channel']!s:>3}  {n['auth']:<40} {n['ssid']}")
    print(s.adapters)
    assert all(len(n["mac"]) == 17 and n["channel"] for n in nets)
