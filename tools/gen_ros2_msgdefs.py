"""Regenerates src/allsense/_ros2_msgdefs.py (MCAP export) from a ROS 2 Humble install, e.g. in WSL:
    python3 tools/gen_ros2_msgdefs.py > src/allsense/_ros2_msgdefs.py"""
import os
import re

SHARE = "/opt/ros/humble/share"
TYPES = ["sensor_msgs/Imu", "sensor_msgs/MagneticField", "sensor_msgs/FluidPressure", "sensor_msgs/Illuminance",
         "sensor_msgs/Temperature", "sensor_msgs/RelativeHumidity", "sensor_msgs/Range", "sensor_msgs/NavSatFix",
         "sensor_msgs/BatteryState", "std_msgs/Float64MultiArray"]
PRIMS = {"bool", "byte", "char", "float32", "float64", "int8", "uint8", "int16", "uint16", "int32", "uint32",
         "int64", "uint64", "string", "wstring"}


def text(t):
    pkg, name = t.split("/")
    with open(f"{SHARE}/{pkg}/msg/{name}.msg") as f:
        return f.read()


def deps(t):
    pkg = t.split("/")[0]
    out = []
    for line in text(t).splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        typ, rest = line.split(None, 1)
        if "=" in rest.split()[0] or re.match(r"^\w+\s*=", rest):
            continue  # constant
        base = re.sub(r"\[.*\]$", "", typ)
        base = re.sub(r"<=\d+$", "", base)
        if base in PRIMS:
            continue
        if base == "Header":
            base = "std_msgs/Header"
        elif "/" not in base:
            base = f"{pkg}/{base}"
        out.append(base)
    return out


def full(t):
    seen, order = set(), []

    def walk(x):
        for d in deps(x):
            if d not in seen:
                seen.add(d)
                order.append(d)
                walk(d)

    walk(t)
    def bare(x):  # without comments and blank lines
        lines = (re.sub(r"\s+#.*$", "", l).rstrip() for l in text(x).splitlines() if not l.lstrip().startswith("#"))
        return "\n".join(l for l in lines if l)

    parts = [bare(t)]
    for d in order:
        parts.append("=" * 80 + f"\nMSG: {d}\n" + bare(d))
    return "\n".join(parts) + "\n"


print('"""ROS 2 Humble message definitions (comments removed) for MCAP export, generated from /opt/ros/humble/share/*/msg."""\n')
print("MSGDEFS = {")
for t in TYPES:
    print(f"    {t.replace('/', '/msg/')!r}: {full(t)!r},")
print("}")
