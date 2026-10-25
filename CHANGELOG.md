# Changelog

## 0.1.0 (unreleased)

- First release: `allsense.connect()`, streams and subscriptions (`get`, `latest`, `read`, callbacks), clock sync, reconnect with a stall watchdog, cameras (MJPEG, H.264, snapshots), microphones, commands, saved sounds, recordings (`load_recording`) and MCAP export, the `allsense` command line.
- Speaks AllSense protocol version 0.
- `connect(token=…)` without a link no longer sends the token to the first phone discovery finds: with several it raises and lists them; `device=…` picks one by name.
- Error messages and their causes never contain the token (`token=***`).
- Camera snapshots, MJPEG/H.264 parts, header lines and saved-sound replies have size limits (32 MB per frame); larger ones raise `AllSenseError`.
- Media paths from the phone's catalog must start with `/` and contain no `@`, `\` or line breaks.
- `load_recording` reads only meta.json and the CSV files it lists (plain file names), with per-file and total size limits, streaming each CSV.
- `allsense list`/`echo` strip control characters from names the phone sends; `echo --csv` quotes fields with the csv module.
