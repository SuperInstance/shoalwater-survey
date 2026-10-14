#!/usr/bin/env python3
"""Assemble the survey viewer: pins + frames into viewer/data/."""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def main():
    survey = os.path.join(REPO, "survey")
    vdata = os.path.join(REPO, "viewer", "data")
    if os.path.exists(vdata):
        shutil.rmtree(vdata)
    os.makedirs(vdata)
    pins = []
    pins_path = os.path.join(survey, "pins.jsonl")
    if os.path.exists(pins_path):
        pins = [json.loads(l) for l in open(pins_path) if l.strip()]
    with open(os.path.join(vdata, "pins.json"), "w") as f:
        json.dump(pins, f)
    src_frames = os.path.join(survey, "frames")
    if os.path.isdir(src_frames):
        shutil.copytree(src_frames, os.path.join(vdata, "frames"))
    print(f"viewer data: {len(pins)} pins -> {vdata}")


if __name__ == "__main__":
    main()
