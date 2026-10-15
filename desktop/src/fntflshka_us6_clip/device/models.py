from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class Capabilities:
    protocol_version: int
    asset_version: int
    display_width: int
    display_height: int
    pixel_format: int
    reserved: int
    flash_configured: int
    physical_flash: int
    storage_total: int
    storage_free: int
    max_asset_size: int
    max_upload_chunk: int
    render_buffer_size: int

    @property
    def supports_scene_read(self):
        return bool(self.reserved & 1)

    @property
    def supports_scene_metadata(self):
        return bool(self.reserved & 2)

    @property
    def supports_playlist(self):
        return bool(self.reserved & 4)

    @property
    def supports_transitions(self):
        return bool(self.reserved & 8)

    @property
    def supports_diagnostics(self):
        return bool(self.reserved & 16)

    @property
    def supports_animation(self):
        return bool(self.reserved & 32)

    @classmethod
    def decode(cls, data):
        if len(data) != 32:
            raise ValueError("Invalid capabilities length")
        caps = cls(*struct.unpack("<BBHHBBIIIIIHH", data))
        if (caps.protocol_version != 1 or caps.asset_version != 1 or caps.pixel_format != 1
                or not 1 <= caps.display_width <= 320 or not 1 <= caps.display_height <= 172
                or not 1 <= caps.max_upload_chunk <= 512 or caps.storage_free > caps.storage_total):
            raise ValueError("Unsupported capabilities for asset/protocol v1")
        return caps


@dataclass(frozen=True)
class Snapshot:
    port: str
    firmware_version: tuple[int, int]
    capabilities: Capabilities
    scenes: tuple
    device_id: str = ""


def mock_capabilities():
    return Capabilities(1, 1, 320, 172, 1, 0, 4194304, 0, 2031616, 1761280, 110104, 512, 5120)
