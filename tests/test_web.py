import json
import http.client
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest

from ryujin_lcd import web
from ryujin_lcd.web import ApiError, App, Handler, configure_access


def test_sensor_poll_reuses_discovery_and_refreshes_values(tmp_path, monkeypatch):
    from ryujin_lcd import monitor

    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "name").write_text("coretemp")
    (hwmon / "temp1_input").write_text("42000")
    (hwmon / "temp1_label").write_text("Package")
    glob = web.glob.glob
    monkeypatch.setattr(web.glob, "glob", lambda pattern: glob(
        pattern.replace("/sys/class/hwmon", str(tmp_path))))

    def no_rescan(name):
        pytest.fail("sensor poll repeated hwmon discovery")

    monkeypatch.setattr(monitor, "hwmon_path", no_rescan)
    sensors = web.Sensors(False)
    first = sensors.list()
    assert len(first) == 1
    assert first[0]["id"] == "coretemp/temp1"
    assert first[0]["label"] == "Package"
    assert first[0]["value"] == monitor.FORMATS["temp"][1].format(42) + monitor.FORMATS["temp"][2]
    (hwmon / "temp1_input").write_text("43000")
    assert sensors.list()[0]["value"] != first[0]["value"]
    # Re-enumeration must not leave a cached path pointing at the old device.
    hwmon.rename(tmp_path / "hwmon1")
    assert sensors.list()[0]["id"] == "coretemp/temp1"
    (tmp_path / "hwmon1" / "temp1_input").unlink()
    assert sensors.list() == []


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Never read or write the developer's own ~/.config and ~/.local/share."""
    monkeypatch.setattr(web, "CONFIG", str(tmp_path / "web.json"))
    monkeypatch.setattr(web, "DATA_DIR", str(tmp_path / "data"))


@contextmanager
def running_server(app, *, allowed_hosts={"127.0.0.1", "localhost", "::1"}, auth_token=None):
    Handler.app = app
    Handler.allowed_hosts = allowed_hosts
    Handler.auth_token = auth_token
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request_json(base, path, body, headers=None):
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def get_json(base, path, headers=None):
    request = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_raw_command_reaches_the_device():
    app = App(demo=True)

    assert app.raw({"hex": "DC"})["sent"].startswith("EC DC")


def test_leave_parks_the_lcd_on_the_power_on_default():
    app = App(demo=True)
    app.show({"source": "gif", "slots": [4], "duration": 5})
    app.set_boot({"source": "gif", "slot": 4})
    app.hwmon({"lines": [{"label": "CPU", "value": "1"}]})

    assert app.leave() == "gif 4"
    assert app.device.dev.current_item() == (web.KIND["gif"], web.MTYPE["gif"], 4)
    assert App(demo=True).config["boot"] == {"source": "gif", "slot": 4}


def test_leave_is_a_no_op_without_a_default():
    app = App(demo=True)
    app.hwmon({"lines": [{"label": "CPU", "value": "1"}]})

    assert app.leave() is None
    assert app.device.dev.current_item()[0] == web.MODE_HWMON


def test_restore_reapplies_a_single_animation_only_when_parked():
    app = App(demo=True)
    app.show({"source": "gif", "slots": [4], "duration": 5})
    app.device.dev.mode(web.MODE_HWMON)          # what the LCD shows after a stop
    assert App(demo=True).restore() is None      # no default: the cooler kept the animation itself

    app.set_boot({"source": "gif", "slot": 4})
    parked = App(demo=True)
    assert parked.restore() == "slideshow"
    assert parked.device.dev.current_item() == (web.KIND["gif"], web.MTYPE["gif"], 4)


@pytest.mark.parametrize("body", [{"source": "clock", "slot": 1}, {"source": "gif", "slot": 16}])
def test_set_boot_rejects_bad_targets(body):
    with pytest.raises(ApiError):
        App(demo=True).set_boot(body)


def test_generic_config_endpoint_is_not_exposed():
    with running_server(App(demo=True)) as base:
        status, body = request_json(base, "/api/config", {"hwmon": None})

    assert status == 404
    assert body == {"error": "not found"}


def test_structurally_invalid_saved_config_falls_back_to_defaults(tmp_path, monkeypatch):
    config = tmp_path / "web.json"
    config.write_text('{"mode":"hwmon","hwmon":null}')
    monkeypatch.setattr(web, "CONFIG", str(config))

    loaded = web.load_config()

    assert loaded == web.DEFAULT_CONFIG
    assert loaded is not web.DEFAULT_CONFIG


@pytest.mark.parametrize("update", [
    {"slideshow": {"duration": "not-a-number"}},
    {"hwmon": {"lines": [{"label": "CPU", "sensor": 123}]}},
    {"hwmon": {"bg": None}},
    {"hwmon": {"interval": "not-a-number"}},
    {"slideshow": {"duration": float("inf")}},
    {"slideshow": {"gif_slots": ["bad"]}},
    {"slideshow": {"gif_slots": [float("inf")]}},
    {"slideshow": {"banner": {"color": None}}},
    {"slideshow": {"banner": {"x": "bad"}}},
])
def test_malformed_nested_saved_config_falls_back_to_defaults(tmp_path, monkeypatch, update):
    config = tmp_path / "web.json"
    config.write_text(json.dumps(web.merge(web.DEFAULT_CONFIG, update)))
    monkeypatch.setattr(web, "CONFIG", str(config))

    assert web.load_config() == web.DEFAULT_CONFIG


@pytest.mark.parametrize("body", [
    {"source": "clock", "duration": float("inf")},
    {"source": "jpg", "slot": 0, "banner": {"x": "bad"}},
])
def test_show_converts_invalid_numeric_fields_to_api_errors(body):
    with pytest.raises(ApiError):
        App(demo=True).show(body)


def test_loopback_server_rejects_unexpected_host_header():
    with running_server(App(demo=True)) as base:
        status, body = request_json(
            base,
            "/api/display",
            {"brightness": 50},
            headers={"Host": "attacker.example:8686"},
        )

    assert status == 403
    assert "host" in body["error"].lower()


def test_malformed_host_header_is_rejected_cleanly():
    with running_server(App(demo=True)) as base:
        parsed = urllib.parse.urlsplit(base)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1)
        connection.putrequest("GET", "/api/status", skip_host=True)
        connection.putheader("Host", "[")
        connection.endheaders()
        response = connection.getresponse()
        body = json.load(response)
        connection.close()

    assert response.status == 400
    assert "host" in body["error"].lower()


def test_duplicate_host_headers_are_rejected():
    with running_server(App(demo=True)) as base:
        parsed = urllib.parse.urlsplit(base)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1)
        connection.putrequest("GET", "/api/status", skip_host=True)
        connection.putheader("Host", "127.0.0.1")
        connection.putheader("Host", "attacker.example")
        connection.endheaders()
        response = connection.getresponse()
        body = json.load(response)
        connection.close()

    assert response.status == 400
    assert "host" in body["error"].lower()


def test_remote_server_requires_bearer_token_for_api_reads():
    with running_server(App(demo=True), allowed_hosts=None, auth_token="secret") as base:
        denied, _ = get_json(base, "/api/status")
        allowed, body = get_json(base, "/api/status", {"Authorization": "Bearer secret"})

    assert denied == 401
    assert allowed == 200
    assert body["demo"] is True


def test_duplicate_authorization_headers_are_rejected():
    with running_server(App(demo=True), allowed_hosts=None, auth_token="secret") as base:
        parsed = urllib.parse.urlsplit(base)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1)
        connection.putrequest("GET", "/api/status")
        connection.putheader("Authorization", "Bearer secret")
        connection.putheader("Authorization", "Bearer secret")
        connection.endheaders()
        response = connection.getresponse()
        connection.close()

    assert response.status == 401


def test_non_ascii_authorization_header_is_rejected_cleanly():
    with running_server(App(demo=True), allowed_hosts=None, auth_token="secret") as base:
        parsed = urllib.parse.urlsplit(base)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1)
        connection.request("GET", "/api/status", headers={"Authorization": "Bearer s\N{LATIN SMALL LETTER E WITH ACUTE}cret"})
        response = connection.getresponse()
        body = json.load(response)
        connection.close()

    assert response.status == 401
    assert "authentication" in body["error"].lower()


def test_non_loopback_bind_requires_token():
    with pytest.raises(ApiError, match="token"):
        configure_access("0.0.0.0", None)

    allowed_hosts, token = configure_access("0.0.0.0", "secret")
    assert allowed_hosts is None
    assert token == "secret"


def test_negative_content_length_is_rejected_without_reading_to_eof():
    with running_server(App(demo=True)) as base:
        parsed = urllib.parse.urlsplit(base)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1)
        connection.putrequest("POST", "/api/display")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", "-1")
        connection.endheaders()
        response = connection.getresponse()
        body = json.load(response)
        connection.close()

    assert response.status == 400
    assert "content-length" in body["error"].lower()


def test_delete_is_serialized_with_other_media_actions(monkeypatch):
    app = App(demo=True)
    monkeypatch.setattr(web, "forget_media", lambda *_args: None)
    finished = threading.Event()

    app.action_lock.acquire()
    thread = threading.Thread(target=lambda: (app.delete("gif", 0), finished.set()))
    thread.start()
    try:
        assert not finished.wait(0.05)
    finally:
        app.action_lock.release()
        thread.join(timeout=1)

    assert finished.is_set()


def test_worker_reference_is_retained_when_stop_times_out():
    class StuckWorker:
        def stop(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return True

    for attr, stop in (("monitor", "stop_monitor"), ("player", "stop_player")):
        app = App(demo=True)
        worker = StuckWorker()
        setattr(app, attr, worker)

        with pytest.raises(ApiError, match="did not stop"):
            getattr(app, stop)()

        assert getattr(app, attr) is worker


def test_monitor_stop_is_serialized_with_mode_changes():
    class Worker:
        def stop(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    app = App(demo=True)
    app.monitor = Worker()
    finished = threading.Event()
    app.action_lock.acquire()
    thread = threading.Thread(target=lambda: (app.stop_monitor(), finished.set()))
    thread.start()
    try:
        assert not finished.wait(0.05)
    finally:
        app.action_lock.release()
        thread.join(timeout=1)

    assert finished.is_set()


def test_concurrent_slideshow_changes_leave_only_one_worker(monkeypatch):
    instances = []

    class FakePlayer:
        def __init__(self, device, slots, interval):
            self.slots = slots
            self.interval = interval
            self.alive = False
            self.current = None
            self.error = None
            instances.append(self)

        def start(self):
            self.alive = True

        def stop(self):
            self.alive = False

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return self.alive

    app = App(demo=True)
    app.set_config = lambda **updates: None
    app.read_storage = lambda: {"gif": {"used": [0, 1, 2, 3]}}
    original_stop = app.stop_player

    def slow_stop():
        original_stop()
        time.sleep(0.05)

    app.stop_player = slow_stop
    monkeypatch.setattr(web, "SlideshowPlayer", FakePlayer)
    barrier = threading.Barrier(2)

    def change(slots):
        barrier.wait()
        app.show({"source": "gif", "slots": slots, "duration": 5})

    threads = [threading.Thread(target=change, args=([0, 1],)), threading.Thread(target=change, args=([2, 3],))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(player.is_alive() for player in instances) == 1


def test_import_crate_probe_closes_device_after_disk_error(monkeypatch, tmp_path):
    class Probe:
        def __init__(self):
            self.closed = False

        def disk_info(self):
            raise web.RyujinError("probe failed")

        def close(self):
            self.closed = True

    probe = Probe()
    monkeypatch.setattr(web, "Ryujin", lambda _verbose: probe)
    monkeypatch.setattr(web, "import_crate", lambda _root, _storage: [])
    monkeypatch.setattr(web.sys, "argv", ["ryujin-lcd-web", "--import-crate", str(tmp_path)])

    web.main()

    assert probe.closed


def test_settle_waits_for_slot_and_rejects_unconfirmed_switch():
    class Delayed:
        def __init__(self):
            self.replies = iter([(web.KIND['gif'], 1, 1), (web.KIND['gif'], 1, 2)])
        def current_item(self):
            return next(self.replies)
    web.settle(Delayed(), web.KIND['gif'], slot=2)
    dev = web.DemoRyujin()
    with pytest.raises(ApiError, match='did not switch'):
        web.settle(dev, web.KIND['gif'], timeout=0, slot=15)


def temperature_player():
    app = App(demo=True)
    values = {'k10temp/temp1': '50.0°C', 'rog_ryujin/temp1': '35.0°C', 'rog_ryujin/fan1': '1700 RPM'}
    app.sensors.read = lambda hw, attr: values.get(f'{hw}/{attr}', 'n/a')
    player = web.TemperaturePlayer(app.device, app.sensors, [1, 2, 6],
                                   web.thermal_config({}), app.config['hwmon'])
    return app, player, values


def test_temperature_sustain_hysteresis_takeover_and_recovery():
    app, p, values = temperature_player()
    p.tick(0)
    assert app.device.dev.current_item()[2] == 1
    values['k10temp/temp1'] = '65.0°C'
    p.tick(2)
    p.tick(11)
    assert p.stage == 0
    p.tick(12)
    assert p.stage == 1 and p.current == 2
    values['k10temp/temp1'] = '59.0°C'
    p.tick(30)
    p.tick(50)
    assert p.stage == 1  # return margin prevents oscillation
    values['k10temp/temp1'] = '80.0°C'
    p.tick(52)
    p.tick(62)
    assert p.stage == 2 and p.current == 6
    values['rog_ryujin/temp1'] = '46.0°C'
    p.tick(64)
    p.tick(74)
    assert p.stage == 3 and app.device.dev.current_item()[0] == web.MODE_HWMON
    values['rog_ryujin/temp1'] = '43.0°C'
    p.tick(90)
    assert p.stage == 3
    values['rog_ryujin/temp1'] = '40.0°C'
    p.tick(92)
    p.tick(102)
    assert p.stage == 2 and p.current == 6


def test_missing_sensor_shows_stats_and_recovers_with_dwell():
    app, p, values = temperature_player()
    values['k10temp/temp1'] = 'n/a'
    p.tick(0)
    assert p.stage == 3 and 'unavailable' in p.error
    assert app.device.dev.current_item()[0] == web.MODE_HWMON
    values['k10temp/temp1'] = '50.0°C'
    p.tick(2)
    p.tick(12)
    assert p.stage == 0 and p.error is None


def test_temperature_restore_and_switch_stop_worker():
    app = App(demo=True)
    app.read_storage = lambda: {'gif': {'used': [1, 2, 6]}}
    app.show({'source': 'gif', 'slots': [1, 2, 6], 'behavior': 'temperature'})
    worker = app.player
    app.stop_player()
    restored = App(demo=True)
    restored.read_storage = app.read_storage
    try:
        assert restored.restore() == 'slideshow'
        assert restored.player_state()['behavior'] == 'temperature'
        restored.show({'source': 'gif', 'slot': 6})
        assert restored.player is None
        assert restored.config['slideshow']['behavior'] == 'rotate'
    finally:
        restored.stop_player()
    assert not worker.is_alive()


@pytest.mark.parametrize('body', [
    {'slots': [4], 'behavior': 'temperature'},
    {'slots': [4], 'behavior': 'unknown'},
    {'slots': [1, 2, 6], 'behavior': 'temperature', 'thermal': {'warm': 90}},
    {'slots': [4], 'thermal': {'cpu': 'k10temp/fan1'}},
    {'slots': [15]},
])
def test_invalid_slideshow_does_not_stop_existing_display(body):
    app = App(demo=True)
    app.show({'source': 'gif', 'slot': 4})
    with pytest.raises(ApiError):
        app.show(dict(body, source='gif'))
    assert app.device.dev.current_item()[2] == 4
    assert app.config['slideshow']['gif_slots'] == [4]


def test_timed_rotation_reaches_each_selected_slot():
    device = web.Device(demo=True)
    player = web.SlideshowPlayer(device, [1, 2, 6], 1)
    visited = []
    class ThreeSteps:
        def is_set(self):
            return len(visited) == 3
        def wait(self, interval):
            visited.append(device.dev.current_item()[2])
    player.stop_event = ThreeSteps()
    player.run()
    assert visited == [1, 2, 6]
    assert player.error is None


def test_active_slideshow_media_cannot_be_deleted():
    app, player, _ = temperature_player()
    app.player = player
    with pytest.raises(ApiError, match='active slideshow'):
        app.delete('gif', 2)
