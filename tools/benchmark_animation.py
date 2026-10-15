"""Profile installed static scene for animation planning; no content writes."""
import argparse
import json
from pathlib import Path
import statistics
import struct
import sys
import time
from serial.tools import list_ports

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop/src"))
from fntflshka_us6_clip.device.discovery import Discovery
from fntflshka_us6_clip.core.protocol import COMMANDS


FIELDS = ("record_version", "heap_free_before", "heap_largest_before", "heap_min_before",
          "internal_free_before", "dma_free_before", "validate_us", "scene_id", "checksum",
          "width", "height", "payload_bytes", "read_samples", "read_us_total", "render_samples",
          "render_us_total", "scratch_bytes", "heap_free_after", "heap_largest_after", "heap_min_after")


def decode_metrics(data):
    if len(data) != 80:
        raise ValueError("Invalid benchmark record length")
    result = dict(zip(FIELDS, struct.unpack("<20I", data)))
    if (result["record_version"] != 1 or not 1 <= result["width"] <= 320 or
            not 1 <= result["height"] <= 172 or
            result["payload_bytes"] != result["width"] * result["height"] * 2 or
            result["read_samples"] != 5 or result["render_samples"] != 3 or
            not result["read_us_total"] or not result["render_us_total"] or
            result["scratch_bytes"] != 5120):
        raise ValueError("Invalid benchmark metrics")
    result["read_ms_per_frame"] = result["read_us_total"] / result["read_samples"] / 1000
    result["render_ms_per_frame"] = result["render_us_total"] / result["render_samples"] / 1000
    result["read_bytes_per_second"] = result["payload_bytes"] * result["read_samples"] * 1e6 / result["read_us_total"]
    result["measured_render_fps_ceiling"] = result["render_samples"] * 1e6 / result["render_us_total"]
    return result


def measure(client, snapshot):
    if not snapshot.capabilities.supports_diagnostics:
        raise ValueError("Для замеров нужна диагностическая firmware 1.5.")
    samples = []
    for _ in range(12):
        start = time.perf_counter()
        if client.transport.request(COMMANDS["ping"], b"animation-probe") != b"animation-probe":
            raise ValueError("Invalid PING echo")
        samples.append((time.perf_counter() - start) * 1000)
    metrics = decode_metrics(client.transport.request(COMMANDS["bench_active"], timeout=20))
    scene = next((s for s in snapshot.scenes if s.id == metrics["scene_id"] and s.checksum == metrics["checksum"]), None)
    if scene is None:
        raise ValueError("Scene changed during diagnostic")
    start = time.perf_counter()
    data = client.read_scene(scene, snapshot.capabilities)
    seconds = time.perf_counter() - start
    metrics.update(usb_ping_median_ms=statistics.median(samples), usb_ping_max_ms=max(samples),
                   usb_asset_bytes=len(data), usb_read_seconds=seconds,
                   usb_read_bytes_per_second=len(data) / seconds)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--report", type=Path, default=Path(".pio/animation-benchmark.json"))
    args = parser.parse_args()
    found = Discovery(enumerate_ports=lambda: [p for p in list_ports.comports()
                      if p.device.casefold() == args.port.casefold()]).find() if args.port else Discovery().find()
    if found is None:
        raise SystemExit("Device not found")
    client, snapshot = found
    try:
        report = dict(port=snapshot.port, firmware=snapshot.firmware_version,
                      storage_total=snapshot.capabilities.storage_total,
                      storage_free=snapshot.capabilities.storage_free, metrics=measure(client, snapshot))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        client.transport.close()


if __name__ == "__main__":
    main()
