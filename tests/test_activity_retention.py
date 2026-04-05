import asyncio
from collections import OrderedDict

from argus.web import server


def _build_device(mac: str, packets: int) -> dict:
    return {
        "kismet.device.base.macaddr": mac,
        "kismet.device.base.name": "",
        "kismet.device.base.commonname": "",
        "kismet.device.base.type": "Wi-Fi AP",
        "kismet.device.base.phyname": "IEEE802.11",
        "kismet.device.base.signal/kismet.common.signal.last_signal": -50,
        "kismet.device.base.signal/kismet.common.signal.max_signal": -45,
        "kismet.device.base.channel": "6",
        "kismet.device.base.frequency": 2437,
        "kismet.device.base.first_time": 0,
        "kismet.device.base.last_time": 0,
        "kismet.device.base.packets.total": packets,
        "dot11.device/dot11.device.last_beaconed_ssid_record/dot11.advertisedssid.ssid": "",
    }


def _reset_state() -> None:
    server._device_first_seen = OrderedDict()
    server._last_device_snapshot = OrderedDict()
    server._hunt_last_snapshot = OrderedDict()


def test_activity_retention_caps_maps_during_device_ingest(monkeypatch):
    _reset_state()
    monkeypatch.setattr(server, "_ACTIVITY_MAX_ENTRIES", 100)
    monkeypatch.setattr(server, "_SNAPSHOT_MAX_ENTRIES", 80)
    monkeypatch.setattr(server, "_ACTIVITY_MAX_AGE_SEC", 3600)
    monkeypatch.setattr(server, "_SNAPSHOT_MAX_AGE_SEC", 3600)
    monkeypatch.setattr(server, "classify_device", lambda *_args, **_kwargs: {"manufacturer": "x", "category": "y", "icon": "z"})

    devices = [_build_device(f"02:00:00:00:{i // 256:02x}:{i % 256:02x}", i + 1) for i in range(250)]
    monkeypatch.setattr(server.ks, "post", lambda *_args, **_kwargs: devices)

    result = asyncio.run(server.get_devices())

    assert len(result) == 250
    assert len(server._device_first_seen) <= 100
    assert len(server._last_device_snapshot) <= 80


def test_activity_metrics_use_recent_window_without_full_map_scan(monkeypatch):
    _reset_state()
    now = 1_000.0
    monkeypatch.setattr(server, "_ACTIVITY_MAX_ENTRIES", 100)
    monkeypatch.setattr(server, "_ACTIVITY_MAX_AGE_SEC", 10_000)
    monkeypatch.setattr(server, "classify_device", lambda mac, *_args, **_kwargs: {"manufacturer": mac, "category": "device", "icon": "x"})
    monkeypatch.setattr(server.time, "time", lambda: now)

    server._device_first_seen["aa:aa:aa:aa:aa:03"] = now - 301
    server._device_first_seen["aa:aa:aa:aa:aa:02"] = now - 120
    server._device_first_seen["aa:aa:aa:aa:aa:01"] = now - 10

    metrics = asyncio.run(server.get_activity())

    assert metrics["total_seen"] == 3
    assert metrics["recent_1min"] == 1
    assert metrics["recent_5min"] == 2
    assert [item["mac"] for item in metrics["feed"]] == [
        "aa:aa:aa:aa:aa:01",
        "aa:aa:aa:aa:aa:02",
    ]


def test_sse_new_device_event_still_emits_with_pruned_state(monkeypatch):
    _reset_state()
    now = 2_000.0
    monkeypatch.setattr(server, "_ACTIVITY_MAX_ENTRIES", 10)
    monkeypatch.setattr(server, "_ACTIVITY_MAX_AGE_SEC", 300)
    monkeypatch.setattr(server, "classify_device", lambda mac, *_args, **_kwargs: {"manufacturer": f"mfg-{mac[-2:]}", "category": "test", "icon": "x"})
    monkeypatch.setattr(server.time, "time", lambda: now)
    monkeypatch.setattr(server.ks, "check_online", lambda: (True, 7))

    server._device_first_seen["aa:aa:aa:aa:aa:11"] = now - 20
    server._device_first_seen["aa:aa:aa:aa:aa:10"] = now - 4

    response = asyncio.run(server.event_stream())

    async def _read_two_events():
        first = await response.body_iterator.__anext__()
        second = await response.body_iterator.__anext__()
        return first, second

    first_chunk, second_chunk = asyncio.run(_read_two_events())

    assert "event: device_count" in first_chunk
    assert '"count": 7' in first_chunk
    assert "event: new_devices" in second_chunk
    assert "aa:aa:aa:aa:aa:10" in second_chunk
    assert "aa:aa:aa:aa:aa:11" not in second_chunk
