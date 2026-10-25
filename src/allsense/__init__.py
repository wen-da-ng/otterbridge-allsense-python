"""Stream sensors from an Android phone running OtterBridge AllSense.

    import allsense

    phone = allsense.connect("http://192.168.1.20:8765/?token=abcd")   # link from the app (Home → Connect)
    with phone.subscribe("accelerometer", rate=100) as accel:
        for sample in accel:
            print(sample.time, sample.values)

Protocol: https://docs.allsense.otterbridge.org/PROTOCOL/. MIT licensed.
"""

from .client import AllSenseError, Phone, Sample, StreamInfo, Subscription, TokenError, as_arrays, connect
from .discovery import discover
from .mcap_export import export_mcap
from .recording import Recording, RecordedStream, load_recording

__all__ = ["connect", "discover", "as_arrays", "Phone", "Sample", "StreamInfo", "Subscription", "AllSenseError", "TokenError",
           "load_recording", "Recording", "RecordedStream", "export_mcap"]
__version__ = "0.1.0"
PROTOCOL_VERSION = 0
