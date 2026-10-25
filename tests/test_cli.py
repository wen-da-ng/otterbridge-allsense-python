import csv
import io
from types import SimpleNamespace

from allsense.cli import clean, csv_row, main


def test_names_from_the_phone_cant_drive_the_terminal():
    assert clean("accel\x1b]0;pwned\x07\x1b[2J\u202eer") == "accel]0;pwned[2Jer"
    assert clean("m/s² µT") == "m/s² µT"


def test_csv_rows_are_quoted():
    sample = SimpleNamespace(time=1.5, seq=3, values=(0.25, None))
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(csv_row(sample, 'a,"b"\x1b[31m'))
    assert buf.getvalue() == '1.500000,"a,""b""[31m",3,0.25,\n'
    assert next(csv.reader(io.StringIO(buf.getvalue())))[1] == 'a,"b"[31m'


def test_echo_csv_against_the_fake_phone(phone_server, capsys):
    assert main(["echo", phone_server.url, "accelerometer", "--csv", "--count", "3"]) == 0
    rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert rows[0] == ["time", "name", "seq", "values"] and len(rows) == 4 and rows[1][1] == "accelerometer"
