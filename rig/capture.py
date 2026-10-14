#!/usr/bin/env python3
"""Shoalwater survey capture — the kayak rig's recorder.

Records a SESSION: a GPS track (truth time) plus camera frames (laptop time),
with a clock-sync receipt measuring the offset between the two, so the ingest
can marry each frame to the position the GPS swears the rig was at.

Design rules (from the project's own scar tissue):
- GPS time is truth. Laptop time is a witness that gets calibrated, never trusted raw.
- The clock offset is measured at session start and written into session.json.
- Home exclusion: if rig.local.json names a home point + radius, fixes inside
  it are never recorded at all. What was never written cannot leak.
- Sessions are plain files in sessions/<id>/ — append-only, human-inspectable.

Modes:
  --mock            Synthetic paddle around Black Rock (house test; no hardware).
  (default)         Real camera (opencv) + serial NMEA GPS (pyserial), both
                    imported lazily so this file runs anywhere for --mock.

Usage:
  python3 capture.py --mock --date 2026-09-12 --label "pass A"
  python3 capture.py --camera 0 --gps /dev/ttyUSB0 --interval 10
"""
import argparse
import csv
import json
import math
import os
import struct
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# ---------------------------------------------------------------- mock scene
BLACK_ROCK = (57.0515, -135.4274)   # the charted shoal, our house-test reef


def mock_track(date_str, pass_no):
    """A ~24-minute paddle ellipse around Black Rock. Pass 2 never retraces
    pass 1 exactly: centre shifted ~15 m, phase rotated."""
    lat0, lon0 = BLACK_ROCK
    shift = (pass_no - 1) * 0.00018          # ~15-20 m NE on later passes
    clat, clon = lat0 + shift * 0.7, lon0 + shift
    start = datetime.fromisoformat(date_str + "T17:00:00+00:00")
    fixes = []
    steps = 288                              # 5 s fixes over 24 min
    for i in range(steps + 1):
        t = start + timedelta(seconds=5 * i)
        ang = 2 * math.pi * i / steps + (pass_no - 1) * 0.35
        lat = clat + 0.0026 * math.sin(ang)
        lon = clon + 0.0044 * math.cos(ang)
        cog = (math.degrees(ang) + 90 + (pass_no - 1) * 4) % 360
        fixes.append((t, lat, lon, 1.4, cog))   # ~2.7 kn, kayak pace
    return fixes


# ------------------------------------------------------- tiny PNG (stdlib only)
FONT = {
    "0": [7, 5, 5, 5, 7], "1": [2, 6, 2, 2, 7], "2": [7, 1, 7, 4, 7],
    "3": [7, 1, 3, 1, 7], "4": [5, 5, 7, 1, 1], "5": [7, 4, 7, 1, 7],
    "6": [7, 4, 7, 5, 7], "7": [7, 1, 2, 2, 2], "8": [7, 5, 7, 5, 7],
    "9": [7, 5, 7, 1, 7], "A": [2, 5, 7, 5, 5], "B": [6, 5, 6, 5, 6],
    "C": [3, 4, 4, 4, 3], "E": [7, 4, 6, 4, 7], "F": [7, 4, 6, 4, 4],
    "K": [5, 5, 6, 5, 5], "M": [5, 7, 7, 5, 5], "O": [2, 5, 5, 5, 2],
    "P": [6, 5, 6, 4, 4], "R": [6, 5, 6, 5, 5], "S": [3, 4, 2, 1, 6],
    "T": [7, 2, 2, 2, 2], "-": [0, 0, 7, 0, 0], " ": [0, 0, 0, 0, 0],
    ":": [0, 2, 0, 2, 0], ".": [0, 0, 0, 0, 2],
}


def write_png(path, w, h, rgb_fn, label=""):
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            row += bytes(rgb_fn(x, y))
        rows.append(bytes(row))
    if label:
        scale = 3
        lx, ly = 12, 12
        for ci, ch in enumerate(label.upper()):
            glyph = FONT.get(ch)
            if not glyph:
                continue
            for gy in range(5):
                for gx in range(3):
                    if glyph[gy] >> (2 - gx) & 1:
                        for dy in range(scale):
                            for dx in range(scale):
                                px, py = lx + ci * 4 * scale + gx * scale + dx, ly + gy * scale + dy
                                if 0 <= px < w and 0 <= py < h:
                                    r = bytearray(rows[py])
                                    r[1 + px * 3:1 + px * 3 + 3] = b"\xff\xff\xff"
                                    rows[py] = bytes(r)
    raw = b"".join(rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def mock_frame(path, idx, total, pass_no, t):
    """A stand-in 'bottom portrait': layered strata bands whose hue shifts
    between passes — the bloom, faked honestly and labelled as mock."""
    base = [(28, 74, 84), (36, 96, 92), (52, 118, 88), (74, 128, 76)][pass_no % 4]
    drift = (pass_no - 1) * 14

    def px(x, y):
        band = (y // 36 + (x // 90)) % 3
        r = max(0, min(255, base[0] + band * 9 + drift))
        g = max(0, min(255, base[1] + band * 7))
        b = max(0, min(255, base[2] - band * 6))
        if (x * 7 + y * 13 + idx * 31) % 401 == 0:
            return (210, 200, 150)          # a shell, a fleck of life
        return (r, g, b)

    write_png(path, 320, 180, px,
              label=f"MOCK P{pass_no} F{idx:02d} {t.strftime('%m%d %H%M')}")


# ------------------------------------------------------------------- session
def load_rig_local():
    p = os.path.join(REPO, "rig.local.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


def inside_home(lat, lon, rig):
    home = rig.get("home")
    if not home:
        return False
    radius = rig.get("exclude_m", 250)
    mlon = 111320.0 * math.cos(math.radians(home["lat"]))
    dx = (lon - home["lon"]) * mlon
    dy = (lat - home["lat"]) * 111320.0
    return math.hypot(dx, dy) < radius


def run_session(args):
    rig = load_rig_local()
    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.mock:
        session_id = args.date.replace("-", "") + f"-mock-pass{args.pass_no}"
    sdir = os.path.join(REPO, "sessions", session_id)
    os.makedirs(os.path.join(sdir, "frames"), exist_ok=True)

    if args.mock:
        fixes = mock_track(args.date, args.pass_no)
        clock_offset_s = 47.0               # simulated laptop clock error
        frames_meta = []
        n_frames = 24
        for i in range(n_frames):
            true_t = fixes[0][0] + timedelta(seconds=60 * i)
            laptop_t = true_t + timedelta(seconds=clock_offset_s)
            fp = os.path.join(sdir, "frames", f"frame-{i:03d}.png")
            mock_frame(fp, i, n_frames, args.pass_no, true_t)
            frames_meta.append({"file": f"frames/frame-{i:03d}.png",
                                  "laptop_t": laptop_t.isoformat()})
        source = "mock"
    else:
        raise SystemExit(
            "Real-hardware capture lands with the kayak-day wiring; "
            "today the house test runs on --mock. (Camera/GPS adapters are "
            "lazy-imported in this file's design; see README.)")

    kept = [f for f in fixes if not inside_home(f[1], f[2], rig)]
    dropped = len(fixes) - len(kept)
    with open(os.path.join(sdir, "track.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_utc", "lat", "lon", "sog_kn", "cog_deg"])
        for t, lat, lon, sog, cog in kept:
            w.writerow([t.isoformat(), f"{lat:.7f}", f"{lon:.7f}", sog, f"{cog:.1f}"])
    with open(os.path.join(sdir, "frames.jsonl"), "w") as f:
        for m in frames_meta:
            f.write(json.dumps(m) + "\n")
    meta = {
        "session_id": session_id,
        "label": args.label or f"pass {args.pass_no}",
        "source": source,
        "clock": {"gps_is_truth": True,
                  "laptop_minus_gps_s": clock_offset_s,
                  "note": "frame laptop_t values are corrected by this offset at ingest"},
        "home_exclusion": {"active": bool(rig.get("home")),
                           "fixes_dropped": dropped},
        "frames": len(frames_meta),
        "track_fixes": len(kept),
    }
    with open(os.path.join(sdir, "session.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"session {session_id}: {len(kept)} fixes, {len(frames_meta)} frames, "
          f"clock offset {clock_offset_s:+.1f}s, home-dropped {dropped}")
    print(f"  -> {sdir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--date", default="2026-10-14", help="mock pass date (YYYY-MM-DD)")
    ap.add_argument("--pass-no", type=int, default=1, dest="pass_no")
    ap.add_argument("--label", default="")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--gps", default="")
    ap.add_argument("--interval", type=float, default=10.0)
    run_session(ap.parse_args())


if __name__ == "__main__":
    main()
