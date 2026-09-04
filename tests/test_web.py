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


def test_raw_commands_are_disabled_by_default():
    app = App(demo=True)

    with pytest.raises(ApiError, match="disabled") as exc:
        app.raw({"hex": "DC"})

    assert exc.value.status == 403


def test_raw_media_uploads_are_disabled_by_default():
    app = App(demo=True)

    with pytest.raises(ApiError, match="raw media uploads are disabled") as exc:
        app._prepare_upload({"type": "gif", "slot": "0", "raw": "1"}, b"GIF8not-an-image")

    assert exc.value.status == 403


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
])
def test_malformed_nested_saved_config_falls_back_to_defaults(tmp_path, monkeypatch, update):
    config = tmp_path / "web.json"
    config.write_text(json.dumps(web.merge(web.DEFAULT_CONFIG, update)))
    monkeypatch.setattr(web, "CONFIG", str(config))

    assert web.load_config() == web.DEFAULT_CONFIG


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


def test_remote_server_requires_bearer_token_for_api_reads():
    with running_server(App(demo=True), allowed_hosts=None, auth_token="secret") as base:
        denied, _ = get_json(base, "/api/status")
        allowed, body = get_json(base, "/api/status", {"Authorization": "Bearer secret"})

    assert denied == 401
    assert allowed == 200
    assert body["demo"] is True


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
