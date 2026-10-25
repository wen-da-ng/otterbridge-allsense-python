"""The shortest program: print the phone's accelerometer.

    python quickstart.py "http://192.168.1.20:8765/?token=abcd"
"""
import sys

import allsense

phone = allsense.connect(sys.argv[1])  # the link from the app
for s in phone.subscribe("accelerometer"):
    print(s.values)
