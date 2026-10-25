"""Steer towards an AprilTag.
First turn on Robot → AprilTags in the app and set the tag size.

    python follow_tag.py "http://192.168.1.20:8765/?token=abcd"
"""
import sys

import allsense

phone = allsense.connect(sys.argv[1])
tags = phone.subscribe("vision.apriltag")
while True:
    try:
        tag = tags.get(timeout=0.5)
    except TimeoutError:
        print("No tag in view: stop")
        continue
    if tag["distance"] is None:
        print(f"Tag {tag['id']:.0f}: set the tag size in the app")
        continue
    b = tag["bearing"]  # degrees, left is positive
    turn = "left" if b > 5 else "right" if b < -5 else "straight on"
    print(f"Tag {tag['id']:.0f}: {tag['distance']:.2f} m, {b:+.0f}° → {turn}")
