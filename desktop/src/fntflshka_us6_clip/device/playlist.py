"""Optional protocol v1 playlist extension. No Qt dependencies."""
from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class Playlist:
    enabled: bool = False
    interval_ms: int = 5000
    ids: tuple = ()
    transition: int = 0

    def encode(self):
        if (type(self.enabled) is not bool or not 1000 <= self.interval_ms <= 86400000
                or len(self.ids) > 8 or len(set(self.ids)) != len(self.ids)
                or any(type(i) is not int or not 1 <= i <= 8 for i in self.ids)
                or type(self.transition) is not int or not 0 <= self.transition <= 5
                or (self.enabled and not self.ids)):
            raise ValueError("Неверный плейлист: 1–8 разных сцен, интервал 1–86400 секунд.")
        return struct.pack("<BBBBI8I2x", 1, self.enabled, len(self.ids), 0,
                           self.interval_ms, *(self.ids + (0,) * (8 - len(self.ids))))

    @classmethod
    def decode(cls, data):
        if len(data) != 42:
            raise ValueError("Неверная длина плейлиста.")
        version, enabled, count, reserved, interval, *ids = struct.unpack("<BBBBI8I2x", data)
        if version != 1 or enabled > 1 or reserved or count > 8 or any(ids[count:]) or any(data[40:]):
            raise ValueError("Неверный ответ плейлиста.")
        result = cls(bool(enabled), interval, tuple(ids[:count]))
        result.encode()
        return result
