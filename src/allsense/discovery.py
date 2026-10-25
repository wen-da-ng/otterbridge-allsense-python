"""Find AllSense phones on the local network (DNS-SD ``_allsense._tcp``). Needs ``zeroconf``."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

SERVICE_TYPE = "_allsense._tcp.local."


@dataclass(frozen=True)
class FoundPhone:
    name: str  # e.g. "AllSense rover"
    device: str  # the device name set in the app
    host: str
    port: int
    protocol: str

    @property
    def url(self) -> str:
        """Base URL; add the token: ``allsense.connect(found.url, token="…")``."""
        return f"http://{self.host}:{self.port}/"


def discover(timeout: float = 3.0) -> List[FoundPhone]:
    """Phones currently streaming on this network, found within ``timeout`` seconds."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError as e:
        raise ImportError("Discovery needs zeroconf: pip install \"allsense[discovery]\"") from e

    found = {}

    class Listener(ServiceListener):
        def add_service(self, zc: "Zeroconf", type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name, timeout=int(timeout * 1000))
            if info is None:
                return
            addresses = info.parsed_addresses()
            if not addresses:
                return
            props = {k.decode(): (v or b"").decode() for k, v in info.properties.items()}
            # Prefer IPv4: it's what phones and robots route reliably.
            host = next((a for a in addresses if ":" not in a), addresses[0])
            found[name] = FoundPhone(
                name=name.split("." + type_)[0],
                device=props.get("device", ""),
                host=host,
                port=info.port,
                protocol=props.get("protocol", ""),
            )

        def update_service(self, zc: "Zeroconf", type_: str, name: str) -> None:
            self.add_service(zc, type_, name)

        def remove_service(self, zc: "Zeroconf", type_: str, name: str) -> None:
            found.pop(name, None)

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, SERVICE_TYPE, Listener())
        time.sleep(timeout)
    finally:
        zc.close()
    return sorted(found.values(), key=lambda p: p.name)
