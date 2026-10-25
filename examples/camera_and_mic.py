"""Take a photo and listen for a moment: the phone's first camera and microphone.

    python camera_and_mic.py "http://192.168.1.20:8765/?token=abcd"
"""
import math
import sys

import allsense

phone = allsense.connect(sys.argv[1])

camera = phone.cameras()[0].name
with open("photo.jpg", "wb") as f:
    f.write(phone.snapshot(camera))
print(f"Saved photo.jpg from {camera}")

mic = phone.microphones()[0].name
with phone.microphone(mic) as audio:
    for i, chunk in enumerate(audio):  # 0.1 s each
        rms = math.sqrt(sum(x * x for x in chunk.samples) / len(chunk.samples))
        print(f"{mic}: {20 * math.log10(max(rms, 1) / 32768):.0f} dBFS")
        if i == 9:
            break
