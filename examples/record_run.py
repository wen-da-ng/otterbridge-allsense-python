"""Record a run on the phone: IMU and GPS for 30 s (or the seconds you give).
First allow Robot → Robot can control the phone in the app.
The files stay on the phone: Robot → Recording → Share.

    python record_run.py "http://192.168.1.20:8765/?token=abcd" [seconds]
"""
import sys
import time

import allsense

phone = allsense.connect(sys.argv[1])
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30
streams = ["accelerometer", "gyroscope", "position"]

phone.command("record", action="start", streams=streams, rate_hz=100)
print(f"Recording for {seconds:.0f} s…")
time.sleep(seconds)
phone.command("record", action="stop")
print("Saved on the phone")
