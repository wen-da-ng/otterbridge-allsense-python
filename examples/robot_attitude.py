"""The robot's tilt and heading (not the phone's).
First set Robot → Mount on the robot in the app.

    python robot_attitude.py "http://192.168.1.20:8765/?token=abcd"
"""
import sys

import allsense

phone = allsense.connect(sys.argv[1])
for s in phone.subscribe("robot.attitude", rate=10):
    heading = "no compass" if s["heading"] is None else f"{s['heading']:5.1f}°"
    print(f"roll {s['roll']:+6.1f}°  pitch {s['pitch']:+6.1f}°  heading {heading}")
