#!/usr/bin/env python3
"""Shoalwater survey ingest — marry frames to positions, file them as pins.

For each session: frame laptop-times are corrected by the session's measured
clock offset (GPS time is truth), positions are interpolated from the track,
and the pack's own tiles supply the rough depth + bottom type + confidence at
that point. Pins are APPENDED to survey/pins.jsonl — passes never overwrite
each other; the fog of time is the record itself.

Usage:
  python3 ingest.py --pack ../shoalwater-full-repo/pack [--sessions sessions]
"""
import argparse
import csv
import json
import math
import os
import shutil
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import qm  # noqa: E402  (vendored decoder from SuperInstance/shoalwater)

ORIGIN = {"lat": 57.05, "lon": -135.43}
MLON = 111320.0 * math.cos(math.radians(ORIGIN["lat"]))
MLAT = 111320.0
BOTTOM = {1: "rock", 2: "sand", 3: "mud"}


def project(lat, lon):
    return (lon - ORIGIN["lon"]) * MLON, (lat - ORIGIN["lat"]) * MLAT


def shelf(depth_m):
    if depth_m is None:
        return "unfiled"
    if depth_m <= 0:
        return "dries"
    if depth_m <= 2:
        return "0-2 m"
    if depth_m <= 5:
        return "2-5 m"
    if depth_m <= 10:
        return "5-10 m"
    return "10+ m"


class Pack:
    def __init__(self, root):
        self.root = root
        with open(os.path.join(root, "tiles-index.json")) as f:
            self.tiles = json.load(f)["tiles"]
        self.by_z = {}
        for t in self.tiles:
            self.by_z.setdefault(t["z"], []).append(t)
        self.cache = {}

    def sample(self, lat, lon):
        """Rough depth at a point from the finest tile that covers it:
        nearest decoded vertex. Returns (depth_m, bottom, confidence)."""
        x, y = project(lat, lon)
        for z in sorted(self.by_z, reverse=True):
            for t in self.by_z[z]:
                if (t["westX"] <= x <= t["westX"] + t["spanX"]
                        and t["southY"] <= y <= t["southY"] + t["spanY"]):
                    d = self._tile(t)
                    if d is None:
                        continue
                    u = (x - t["westX"]) / t["spanX"] * 32767
                    v = (y - t["southY"]) / t["spanY"] * 32767
                    du = d["u"].astype(float) - u
                    dv = d["v"].astype(float) - v
                    i = int((du * du + dv * dv).argmin())
                    h = float(d["height"][i])
                    bottom = BOTTOM.get(int(d["bottom_type"][i]), None) if len(d["bottom_type"]) else None
                    conf = int(d["confidence"][i]) if len(d["confidence"]) else None
                    return -h, bottom, conf
        return None, None, None

    def _tile(self, t):
        key = (t["z"], t["x"], t["y"])
        if key not in self.cache:
            p = os.path.join(self.root, "tiles", str(t["z"]), str(t["x"]),
                             f"{t['y']}.terrain")
            try:
                with open(p, "rb") as f:
                    self.cache[key] = qm.decode_tile(f.read())
            except FileNotFoundError:
                self.cache[key] = None
        return self.cache[key]


def interp(track, t):
    """track: list of (datetime, lat, lon). Linear between fixes."""
    if t <= track[0][0]:
        return track[0][1], track[0][2]
    if t >= track[-1][0]:
        return track[-1][1], track[-1][2]
    for (t0, la0, lo0), (t1, la1, lo1) in zip(track, track[1:]):
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return la0 + (la1 - la0) * f, lo0 + (lo1 - lo0) * f
    return track[-1][1], track[-1][2]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--sessions", default=os.path.join(REPO, "sessions"))
    ap.add_argument("--out", default=os.path.join(REPO, "survey"))
    args = ap.parse_args()

    pack = Pack(args.pack)
    os.makedirs(os.path.join(args.out, "frames"), exist_ok=True)
    pins_path = os.path.join(args.out, "pins.jsonl")
    done = set()
    if os.path.exists(pins_path):
        with open(pins_path) as f:
            for line in f:
                done.add(json.loads(line)["pin_id"])

    sources = []   # (sid, meta, track, frames, sdir-or-None)
    for name in sorted(os.listdir(args.sessions)):
        path = os.path.join(args.sessions, name)
        if os.path.isdir(path):
            with open(os.path.join(path, "session.json")) as f:
                meta = json.load(f)
            track = []
            with open(os.path.join(path, "track.csv")) as f:
                for row in csv.DictReader(f):
                    track.append((datetime.fromisoformat(row["t_utc"]),
                                  float(row["lat"]), float(row["lon"])))
            frames = [json.loads(l) for l in open(os.path.join(path, "frames.jsonl"))]
            sources.append((meta["session_id"], meta, track, frames, path))
        elif name.endswith(".json"):
            # a phone bundle from viewer/field.html: one file, base64 frames
            with open(path) as f:
                bundle = json.load(f)
            meta = {"session_id": bundle["session_id"],
                    "label": bundle.get("label", "phone pass"),
                    "source": bundle.get("source", "phone"),
                    "clock": bundle["clock"]}
            track = [(datetime.fromisoformat(f["t_utc"]), float(f["lat"]),
                      float(f["lon"])) for f in bundle["track"]]
            sources.append((bundle["session_id"], meta, track,
                            bundle["frames"], None))

    new_pins = 0
    for sid, meta, track, frames, sdir in sources:
        offset = meta["clock"]["laptop_minus_gps_s"]
        for i, fr in enumerate(frames):
            pin_id = f"{sid}:{i:03d}"
            if pin_id in done:
                continue
            laptop_t = datetime.fromisoformat(fr["laptop_t"])
            true_t = laptop_t - timedelta(seconds=offset)
            lat, lon = interp(track, true_t)
            depth, bottom, conf = pack.sample(lat, lon)
            dest_dir = os.path.join(args.out, "frames", sid)
            os.makedirs(dest_dir, exist_ok=True)
            if fr.get("jpeg_b64"):
                import base64
                fname = f"frame-{i:03d}.jpg"
                dest = os.path.join(dest_dir, fname)
                with open(dest, "wb") as out:
                    out.write(base64.b64decode(fr["jpeg_b64"]))
                fr = dict(fr, file=fname)
            else:
                dest = os.path.join(dest_dir, os.path.basename(fr["file"]))
                shutil.copyfile(os.path.join(sdir, fr["file"]), dest)
            pin = {
                "pin_id": pin_id, "session": sid, "label": meta.get("label", ""),
                "t_utc": true_t.isoformat(), "lat": round(lat, 7),
                "lon": round(lon, 7),
                "frame": f"frames/{sid}/{os.path.basename(fr['file'])}",
                "depth_est_m": None if depth is None else round(depth, 1),
                "shelf": shelf(depth), "bottom_type": bottom,
                "confidence": conf, "source": meta.get("source", ""),
            }
            with open(pins_path, "a") as f:
                f.write(json.dumps(pin) + "\n")
            new_pins += 1
    print(f"ingest complete: {new_pins} new pins appended to {pins_path}")


if __name__ == "__main__":
    main()
