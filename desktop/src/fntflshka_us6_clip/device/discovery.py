import logging
import time
from serial.tools import list_ports
from ..core.transport import ClipDevice, reset_serial_lines
from .client import DeviceClient

log = logging.getLogger(__name__)


def port_fingerprint(port):
    return (port.device, port.vid, port.pid, port.serial_number)


def ordered_ports(ports):
    return sorted(ports, key=lambda p: (p.vid, p.pid) != (0x303A, 0x1001))


class Discovery:
    def __init__(self, enumerate_ports=list_ports.comports, factory=ClipDevice):
        self.enumerate_ports = enumerate_ports
        self.factory = factory
        self.trusted = set()
        self.recovery_at = {}

    def find(self, cancelled=lambda: False):
        for port in ordered_ports(list(self.enumerate_ports())):
            if cancelled():
                return None
            fingerprint = port_fingerprint(port)
            for recovery in (False, True):
                if recovery:
                    if (fingerprint not in self.trusted or not port.serial_number
                            or (port.vid, port.pid) != (0x303A, 0x1001)
                            or time.monotonic() - self.recovery_at.get(fingerprint, -100) < 30):
                        break
                    self.recovery_at[fingerprint] = time.monotonic()
                transport = None
                try:
                    transport = self.factory(port.device)
                    transport.identity_key = port.serial_number or f"session-{id(transport)}"
                    if recovery:
                        log.warning("USB recovery on previously identified device %r", fingerprint)
                        reset_serial_lines(transport)
                        time.sleep(1.5)
                    client = DeviceClient(transport)
                    snapshot = client.snapshot()
                    self.trusted.add(fingerprint)
                    log.info("Connected %r capabilities=%s", fingerprint, snapshot.capabilities)
                    return client, snapshot
                except ValueError:
                    # An explicit different/unsupported identity must never trigger recovery.
                    self.trusted.discard(fingerprint)
                    log.exception("Rejected device %r", fingerprint)
                    if transport:
                        transport.close()
                    break
                except (OSError, RuntimeError, TimeoutError):
                    log.info("Handshake unavailable port=%s", port.device, exc_info=True)
                    if transport:
                        transport.close()
        return None
