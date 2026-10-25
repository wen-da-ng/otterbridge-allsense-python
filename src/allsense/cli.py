"""Command line: ``allsense discover | list | echo | mcap``."""

from __future__ import annotations

import argparse
import sys
import time

from .client import AllSenseError, connect


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="allsense", description="Stream sensors from an AllSense phone.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", help="find phones on this network")

    ls = sub.add_parser("list", help="list a phone's streams")
    ls.add_argument("url", help="link from the app (Home → Connect) (includes the token)")
    ls.add_argument("--all", action="store_true", help="include vendor-specific sensors, wake-up copies and raw variants")

    echo = sub.add_parser("echo", help="print live samples (CSV with --csv)")
    echo.add_argument("url")
    echo.add_argument("names", nargs="+", help="stream names, e.g. accelerometer gyroscope")
    echo.add_argument("--rate", type=float, default=None, help="Hz for continuous sensors (default 50)")
    echo.add_argument("--count", type=int, default=0, help="stop after this many samples (0 = run until Ctrl+C)")
    echo.add_argument("--csv", action="store_true", help="CSV output: time,name,seq,values…")

    mc = sub.add_parser("mcap", help="convert a recording (.zip or folder) to MCAP for Foxglove or ROS 2")
    mc.add_argument("recording", help="a recording from the phone: allsense_<device>_<time>.zip or its folder")
    mc.add_argument("-o", "--out", help="output file (default: next to the recording, .mcap)")
    mc.add_argument("--ros2", action="store_true", help="ROS 2 messages (ros2 bag play) instead of JSON for Foxglove")

    args = p.parse_args(argv)
    # Units like "m/s²" and "µT": Windows uses a legacy code page when output is piped to a file.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower() not in ("utf-8", "utf8"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.cmd == "discover":
            from .discovery import discover

            found = discover()
            if not found:
                print("No phones found. Is streaming started, and is this computer on the same network?")
            for f in found:
                print(f"{f.name}\tdevice={f.device}\t{f.url}")
            if found:
                # The token is never broadcast; anyone nearby could connect otherwise.
                print("\nAdd the pairing token from the app (Home → Connect), e.g. "
                      f"{found[0].url}?token=…  (or copy the full link shown there)")
            return 0
        if args.cmd == "mcap":
            from .mcap_export import export_mcap

            try:
                out = export_mcap(args.recording, args.out, profile="ros2" if args.ros2 else "foxglove")
            except ImportError:
                print("MCAP export needs: pip install \"allsense[mcap]\"", file=sys.stderr)
                return 1
            print(f"Wrote {out}")
            return 0

        with connect(args.url, name="allsense-cli") as phone:
            if args.cmd == "list":
                streams = phone.streams(all=args.all)
                width = max((len(s.name) for s in streams), default=4)
                print(f"{phone.device}: {len(streams)} streams" + ("" if args.all else " (Basic; --all for everything)"))
                for s in streams:
                    fields = ",".join(s.fields)
                    print(f"  {s.name:<{width}}  {s.type}  [{fields}] {s.unit}".rstrip())
                return 0

            subs = phone.subscribe(args.names, rate=args.rate)
            n = 0
            if args.csv:
                print("time,name,seq,values")
            while args.count == 0 or n < args.count:
                got = False
                for s in subs:
                    budget = None if args.count == 0 else args.count - n
                    if budget == 0:
                        break
                    for sample in s.read(budget):
                        got = True
                        n += 1
                        if args.csv:
                            vals = ",".join("" if v is None else repr(v) for v in sample.values)
                            print(f"{sample.time:.6f},{s.info.name},{sample.seq},{vals}")
                        else:
                            vals = "  ".join(f"{k}={v:.4f}" if v is not None else f"{k}=nan" for k, v in sample.as_dict().items())
                            print(f"{s.info.name:<20} {vals}")
                if not got:
                    time.sleep(0.005)
            return 0
    except AllSenseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
