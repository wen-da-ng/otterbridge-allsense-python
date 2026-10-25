"""Give the robot a face and a voice.
First allow Robot → Robot can control the phone in the app.

    python robot_face.py "http://192.168.1.20:8765/?token=abcd"
"""
import sys
import time

import allsense

phone = allsense.connect(sys.argv[1])
phone.command("display", face="happy", text="Hello!")
phone.command("say", text="Hello, I'm your robot")
phone.command("vibrate", ms=200)
time.sleep(3)
phone.command("display", face="thinking", text="Where next?")
time.sleep(3)
phone.command("display", action="clear")
print("Done")
