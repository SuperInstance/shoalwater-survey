# Shoalwater Survey

The kayak rig: a laptop, a camera on a cable, and a GPS, paddling the shallow
water of Sitka Sound and coming home with **bottom portraits** — photographs of
the seafloor, filed where they were taken, kept in versions forever.

The chart ([shoalwater-full](https://github.com/SuperInstance/shoalwater-full))
knows the Sound's shape from 50 million soundings, but soundings thin out
exactly where a kayak goes: the shallows, the edges, the reef that dries.
And the chart can only *infer* the bottom — rock, sand, mud. A portrait is
the actual gravel, the actual kelp, the actual edge of the eelgrass bed.
Each pass appends; nothing is overwritten. Slide time and watch a reef
bloom and fall.

## The pieces

- `rig/capture.py` — the recorder. GPS track (truth time) + camera frames
  (laptop time) + a **clock-sync receipt**: the offset between the clocks is
  measured every session and written down, never assumed. A **home
  exclusion** (`rig.local.json`) drops fixes near home before they are ever
  written — what was never recorded cannot leak.
- `rig/ingest.py` — marries each frame to its true position (offset applied,
  track interpolated) and asks the pack's tiles for the rough depth, bottom
  type and confidence at that point. Appends pins to `survey/pins.jsonl`.
- `viewer/` — the portraits on the Deployment 1 render, with a time slider.
  Latest pass bright; superseded water dims. Click a pin for the portrait
  and the chart's claims about that spot — the claims the portrait exists
  to check.

## House test (no hardware, no installs — Python 3 only)

```sh
python3 rig/capture.py --mock --date 2026-09-12 --pass-no 1 --label "pass A"
python3 rig/capture.py --mock --date 2026-10-14 --pass-no 2 --label "pass B"
python3 rig/ingest.py --pack /path/to/shoalwater-full/pack
python3 rig/build_viewer.py
python3 -m http.server -d viewer 8791   # open http://localhost:8791
```

Two synthetic paddles around Black Rock, a month apart, with a simulated
47-second camera-clock error for the ingest to correct. If the pins land
and the slider scrubs, the pipeline is proven end to end.

## Or just use your phone (Field mode)

`viewer/field.html` — also live at the Pages site under `/viewer/field.html` —
turns a phone into the whole rig: GPS, camera and clock in one hand. Open it,
press **Start pass**, walk the dock or the garden (house test) or paddle your
line, and it records the track plus a portrait every 15 seconds, measuring
the GPS-vs-phone clock offset as it goes. **End pass**, **Download session**,
then import the file in the viewer: your portraits land on the chart where
you stood. Set home on the field page and a 250 m quiet zone around it never
records at all. The same session file feeds `rig/ingest.py` at home, which
files the chart's depths against your portraits.

## Kayak day

1. Clone this repo on the laptop; clone `shoalwater-full` beside it (the ingest's chart).
2. Copy `rig.local.json.example` to `rig.local.json` and set your home point —
   or don't, and accept that your launch stretch is in your own private sessions.
3. Real-hardware capture (USB/UVC camera + serial NMEA GPS) is the next
   wiring step; the session format it will write is already frozen by the
   mock: `track.csv` (GPS truth), `frames.jsonl` (laptop-stamped frames),
   `session.json` (the clock receipt).

## Privacy

Sessions and survey data are **local by default** (gitignored). Publishing a
pass is a deliberate act, pass by pass. The pack's full-resolution data stays
owner-retained in its own repo under its own rules; this repo's viewer
backdrop is the public web-size render.

Code: Apache-2.0.
