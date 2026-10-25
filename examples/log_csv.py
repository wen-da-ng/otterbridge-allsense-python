"""Record several sensors to one CSV file for 10 seconds.

    python log_csv.py "http://…/?token=…" accelerometer gyroscope magnetic_field
"""
import csv
import sys
import time

import allsense

url, names = sys.argv[1], sys.argv[2:] or ["accelerometer"]
phone = allsense.connect(url)
subs = phone.subscribe(names, rate=100)

with open("allsense_log.csv", "w", newline="") as f:
    out = csv.writer(f)
    out.writerow(["time", "name", "seq", "values"])
    end = time.time() + 10
    while time.time() < end:
        for sub in subs:
            for s in sub.read():
                out.writerow([f"{s.time:.6f}", sub.info.name, s.seq, *s.values])
        time.sleep(0.01)

for sub in subs:
    print(f"{sub.info.name}: dropped {sub.dropped}")
phone.close()
print("Saved allsense_log.csv")
