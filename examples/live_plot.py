"""Live plot of the last 10 seconds of a sensor (needs matplotlib).

    python live_plot.py "http://…/?token=…" gyroscope
"""
import collections
import sys

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

import allsense

phone = allsense.connect(sys.argv[1])
name = sys.argv[2] if len(sys.argv) > 2 else "accelerometer"
sub = phone.subscribe(name, rate=100)
info = sub.info
history = collections.deque(maxlen=1000)  # 10 s at 100 Hz

fig, ax = plt.subplots()
ax.set_title(f"{info.topic}")
ax.set_xlabel("seconds")
ax.set_ylabel(info.unit)
lines = []


def update(_):
    history.extend(sub.read())
    if not history:
        return lines
    times, values = allsense.as_arrays(list(history))
    t = times - times[-1]
    if not lines:
        labels = [info.fields[i] if i < len(info.fields) else f"v{i}" for i in range(values.shape[1])]
        lines.extend(ax.plot(t, values[:, i], label=labels[i])[0] for i in range(values.shape[1]))
        ax.legend(loc="upper left")
    for i, line in enumerate(lines):
        line.set_data(t, values[:, i])
    ax.relim()
    ax.autoscale_view()
    return lines


anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)
plt.show()
phone.close()
