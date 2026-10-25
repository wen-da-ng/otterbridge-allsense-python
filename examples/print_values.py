"""Print live accelerometer values.

    python print_values.py "http://192.168.1.20:8765/?token=abcd"
"""
import sys

import allsense

phone = allsense.connect(sys.argv[1])
print(f"Connected to {phone.device}. Streams:", ", ".join(s.name for s in phone.streams()))

with phone.subscribe("accelerometer", rate=20) as accel:
    for s in accel:
        print(f"x={s['x']:+7.3f}  y={s['y']:+7.3f}  z={s['z']:+7.3f}  m/s²")
