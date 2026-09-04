import os

import pytest

from ryujin_lcd.device import (
    Ryujin,
    RyujinError,
    select_usb_device,
    usb_identity_for_path,
)


def bare_device():
    device = object.__new__(Ryujin)
    device.verbose = False
    device.events = []
    return device


def test_cmd_rejects_empty_payload_with_protocol_error():
    device = bare_device()

    with pytest.raises(RyujinError, match="1 to 64"):
        device.cmd(b"")


def test_cmd_converts_short_hid_reply_to_protocol_error(monkeypatch):
    device = bare_device()
    device.fd = 7
    device._read = lambda _timeout: b"\xec"
    monkeypatch.setattr(os, "write", lambda _fd, data: len(data))

    with pytest.raises(RyujinError, match="short HID report"):
        device.cmd(b"\xdc")


def test_cmd_rejects_command_specific_short_reply(monkeypatch):
    device = bare_device()
    device.fd = 7
    device._read = lambda _timeout: b"\xec\x5c"
    monkeypatch.setattr(os, "write", lambda _fd, data: len(data))

    with pytest.raises(RyujinError, match="short reply"):
        device.cmd(b"\xdc")


def test_cmd_rejects_short_hid_write(monkeypatch):
    device = bare_device()
    device.fd = 7
    monkeypatch.setattr(os, "write", lambda _fd, _data: 1)

    with pytest.raises(RyujinError, match="short HID write"):
        device.cmd(b"\xdc")


def test_read_rejects_truncated_event_before_queueing(monkeypatch):
    device = bare_device()
    device.fd = 7
    monkeypatch.setattr("select.select", lambda *_args: ([device.fd], [], []))
    monkeypatch.setattr(os, "read", lambda _fd, _size: b"\xee")

    with pytest.raises(RyujinError, match="short HID event"):
        device._read(0)

    assert device.events == []


def test_protocol_methods_reject_out_of_range_slots_before_io():
    device = bare_device()
    calls = []
    device.cmd = lambda payload: calls.append(payload)

    with pytest.raises(RyujinError, match="slot must be 0..15"):
        device.delete("gif", 16)

    assert calls == []


def test_play_rejects_invalid_slot_before_io():
    device = bare_device()
    calls = []
    device.cmd = lambda payload: calls.append(payload)

    with pytest.raises(RyujinError, match="slot must be 0..15"):
        device.play("gif", 99)

    assert calls == []


def test_slideshow_rejects_out_of_range_duration_before_io():
    device = bare_device()
    calls = []
    device.cmd = lambda payload: calls.append(payload)

    with pytest.raises(RyujinError, match="duration must be 1..255"):
        device.slideshow_list([("gif", 0)], 0)

    assert calls == []


def test_banner_rejects_invalid_slot_before_io():
    device = bare_device()
    calls = []
    device.cmd = lambda payload: calls.append(payload)

    with pytest.raises(RyujinError, match="slot must be 0..15"):
        device.banner("jpg", 16, [])

    assert calls == []


def test_failed_upload_attempts_to_close_write_transaction():
    device = bare_device()
    calls = []
    device.disk_info = lambda: None

    def command(payload, timeout=3.0):
        payload = bytes(payload)
        calls.append(payload)
        if payload.startswith(b"\x7f\x02"):
            return bytes([0xEC, 0x7F, 0, 0, 16]) + bytes(60)
        return bytes([0xEC, payload[0]]) + bytes(63)

    device.cmd = command
    device.wait_event = lambda *args, **kwargs: True
    device.bulk_write = lambda _data: (_ for _ in ()).throw(RyujinError("bulk failed"))

    with pytest.raises(RyujinError, match="bulk failed"):
        device.upload(b"payload", "gif", 0)

    assert b"\x73\xff" in calls


def test_failed_begin_attempts_to_close_write_transaction():
    device = bare_device()
    calls = []
    device.disk_info = lambda: None

    def command(payload, timeout=3.0):
        payload = bytes(payload)
        calls.append(payload)
        if payload == b"\x73\x01":
            raise RyujinError("begin reply lost")
        return bytes([0xEC, payload[0]]) + bytes(63)

    device.cmd = command
    device.wait_event = lambda *args, **kwargs: True

    with pytest.raises(RyujinError, match="begin reply lost"):
        device.upload(b"payload", "gif", 0)

    assert b"\x73\xff" in calls


def test_upload_rejects_files_larger_than_wire_size():
    class HugeData:
        def __len__(self):
            return 1 << 32

    device = bare_device()
    device.disk_info = lambda: None
    device.cmd = lambda payload, timeout=3.0: bytes([0xEC, bytes(payload)[0]]) + bytes(63)
    device.wait_event = lambda *args, **kwargs: True

    with pytest.raises(RyujinError, match="too large"):
        device.upload(HugeData(), "gif", 0)


def test_upload_rejects_device_chunk_above_transfer_limit():
    device = bare_device()
    device.disk_info = lambda: None

    def command(payload, timeout=3.0):
        payload = bytes(payload)
        if payload.startswith(b"\x7f\x02"):
            return bytes([0xEC, 0x7F, 0, 0x89, 0x13]) + bytes(60)
        return bytes([0xEC, payload[0]]) + bytes(63)

    device.cmd = command
    device.wait_event = lambda *args, **kwargs: True
    device.bulk_write = lambda _data: pytest.fail("invalid chunk size reached bulk transfer")

    with pytest.raises(RyujinError, match="chunk"):
        device.upload(b"payload", "gif", 0)


def test_upload_requires_success_status_in_chunk_acknowledgement():
    device = bare_device()
    prefixes = []
    device.disk_info = lambda: None

    def command(payload, timeout=3.0):
        payload = bytes(payload)
        if payload.startswith(b"\x7f\x02"):
            return bytes([0xEC, 0x7F, 0, 0, 16]) + bytes(60)
        return bytes([0xEC, payload[0]]) + bytes(63)

    def wait_event(prefix, *args, **kwargs):
        prefixes.append(bytes(prefix))
        return bytes(65)

    device.cmd = command
    device.wait_event = wait_event
    device.bulk_write = lambda data: None

    device.upload(b"payload", "gif", 0)

    assert b"\x14\x00\x00" in prefixes


def test_usb_identity_walks_to_matching_physical_parent(tmp_path):
    usb = tmp_path / "1-2"
    hid = usb / "1-2:1.1" / "0003:0B05:1AA2.0001" / "hidraw" / "hidraw0"
    hid.mkdir(parents=True)
    (usb / "idVendor").write_text("0b05\n")
    (usb / "idProduct").write_text("1aa2\n")
    (usb / "busnum").write_text("1\n")
    (usb / "devnum").write_text("7\n")

    assert usb_identity_for_path(str(hid)) == (1, 7, str(usb))


def test_bulk_device_selection_matches_hid_bus_and_address():
    class Usb:
        def __init__(self, bus, address):
            self.bus = bus
            self.address = address

    expected = Usb(1, 7)
    assert select_usb_device([Usb(1, 3), expected], 1, 7) is expected
