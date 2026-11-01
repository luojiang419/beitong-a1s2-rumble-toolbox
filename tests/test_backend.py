import json
import math
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from controller_backend import ControllerService, validate_rumble
from server import LocalHTTPServer, make_handler


class FakeDriver:
    def __init__(self):
        self.attached = True
        self.calls = []
        self.closed = False
        self.stop_event = threading.Event()

    def enumerate(self):
        return [1] if self.attached else []

    def open(self, _):
        return 1

    def describe(self, _):
        return {"name": "Test controller", "rumble": True}

    def sample(self, *_):
        return [0.5], [1], [0]

    def update(self):
        pass

    def connected(self, _):
        return self.attached

    def xinput_slots(self):
        return [0] if self.attached else []

    def triggers(self, _):
        return {"left": 0.2, "right": 0.8}

    def rumble(self, joystick, low, high, duration):
        self.calls.append((low, high, duration))
        if low == high == 0:
            self.stop_event.set()
        return {"ok": True, "error": None}

    def close(self, _):
        pass

    def quit(self):
        self.closed = True


def wait_until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("后端状态未在期限内更新")
        time.sleep(0.01)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.service = ControllerService(lambda: self.driver)
        self.service.start()
        wait_until(lambda: self.service.snapshot()["connected"])

    def tearDown(self):
        self.service.close()
        self.assertTrue(self.driver.closed)

    def test_validation_rejects_nan_overflow_and_unstructured_events(self):
        valid = {"event": "collision", "low": 0.4, "high": 0.7, "duration_ms": 200}
        self.assertEqual(validate_rumble(valid), valid)
        for change in ({"event": []}, {"low": math.nan}, {"high": 2},
                       {"duration_ms": 2000}, {"duration_ms": True}, {"low": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_rumble({**valid, **change})

    def test_collision_priority_and_stop_command(self):
        collision = {"event": "collision", "low": 0.8, "high": 0.4, "duration_ms": 500}
        self.assertTrue(self.service.request("rumble", collision)["ok"])
        result = self.service.request("rumble", {**collision, "event": "engine"})
        self.assertTrue(result["suppressed"])
        self.assertEqual(len(self.driver.calls), 1)
        self.assertTrue(self.service.request("stop")["ok"])
        self.assertEqual(self.driver.calls[-1], (0, 0, 0))

    def test_disconnect_clears_input_and_stops(self):
        wait_until(lambda: self.service.snapshot()["triggers"] is not None)
        self.assertEqual(self.service.snapshot()["triggers"]["right"], 0.8)
        self.service.request("rumble", {"event": "test", "low": 1, "high": 1, "duration_ms": 1000})
        self.driver.attached = False
        wait_until(lambda: not self.service.snapshot()["connected"])
        state = self.service.snapshot()
        self.assertEqual(state["axes"], [])
        self.assertEqual(state["buttons"], [])
        self.assertIsNone(state["device"])
        self.assertIsNone(state["triggers"])
        self.assertEqual(self.driver.calls[-1], (0, 0, 0))

    def test_missing_browser_heartbeat_stops_long_effect(self):
        self.service.request("rumble", {"event": "test", "low": 1, "high": 1, "duration_ms": 1000})
        self.service.reader_time = time.monotonic() - 1
        self.assertTrue(self.driver.stop_event.wait(1))
        self.assertEqual(self.driver.calls[-1], (0, 0, 0))

    def test_effect_expiry_stops_motor(self):
        self.service.request("rumble", {"event": "test", "low": 0.3, "high": 0.8, "duration_ms": 60})
        self.assertTrue(self.driver.stop_event.wait(1))
        self.assertEqual(self.driver.calls[-1], (0, 0, 0))

    def test_launcher_status_does_not_keep_browser_heartbeat_alive(self):
        self.service.request("rumble", {"event": "test", "low": 1, "high": 1, "duration_ms": 1000})
        previous = time.monotonic() - 1
        self.service.reader_time = previous
        self.service.snapshot(heartbeat=False)
        self.assertEqual(self.service.reader_time, previous)
        self.assertTrue(self.driver.stop_event.wait(1))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = FakeDriver()
        cls.service = ControllerService(lambda: cls.driver)
        cls.service.start()
        cls.server = LocalHTTPServer(("127.0.0.1", 0), make_handler(cls.service, 0))
        cls.port = cls.server.server_port
        cls.server.RequestHandlerClass = make_handler(cls.service, cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.service.close()
        cls.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = (response.status, response.read())
        connection.close()
        return result

    def test_real_http_state_and_invalid_event(self):
        status, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("vibration", json.loads(body))
        status, _ = self.request("POST", "/api/rumble", '{"event":[]}', {"Content-Type": "application/json"})
        self.assertEqual(status, 400)

    def test_running_service_rejects_second_listener(self):
        with self.assertRaises(OSError):
            LocalHTTPServer(("127.0.0.1", self.port), make_handler(self.service, self.port))

    def test_foreign_origin_and_host_cannot_control_motor(self):
        payload = json.dumps({"event": "test", "low": 1, "high": 1, "duration_ms": 200})
        for headers in ({"Origin": "https://example.com"}, {"Host": "evil.example"}):
            status, _ = self.request("POST", "/api/rumble", payload, {"Content-Type": "application/json", **headers})
            self.assertEqual(status, 403)

    def test_private_files_and_traversal_not_served(self):
        for path in ("/../controller_backend.py", "/%2e%2e/server.py", "/api/unknown"):
            status, _ = self.request("GET", path)
            self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
