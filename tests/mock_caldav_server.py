"""Hand-rolled, stdlib-only HTTP mock of a Nextcloud-style CalDAV server.

Serves the paths used by `extensions/caldav.ts`:

    /remote.php/caldav/calendars/<user>/             - collection discovery
    /remote.php/caldav/calendars/<user>/<calendar>/  - PROPFIND/REPORT/MKCALENDAR/PUT
    /remote.php/dav/calendars/<user>/tasks           - task list collections

Every response is a fixture XML, so the extension's request layer can be tested
without touching the network. The server binds to 127.0.0.1 on a free port by
default (configurable via MOCK_PORT) and stops when `stop()` is called or when
the last test process exits.

Usage:    python3 mock_caldav_server.py          # start, print the port
          python3 mock_caldav_server.py stop       # stop the running server
"""

import base64
import hashlib
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn, TCPServer

# Fixture responses live next to this file (relative to the test suite root).
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

MOCK_PORT = int(os.environ.get("MOCK_PORT", "0"))
MOCK_USER = os.environ.get("MOCK_USER", "testuser")
MOCK_PASSWORD = os.environ.get("MOCK_PASSWORD", "testpassword")
# PUT/MKCALENDAR responses default to Nextcloud's 201; set a different code
# (e.g. 500) to exercise the extension's error path for non-2xx replies.
MOCK_PUT_STATUS = int(os.environ.get("MOCK_PUT_STATUS", "201"))

LISTING = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/remote.php/caldav/calendars/%(user)s/JanEsser/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>JanEsser</d:displayname>
        <d:resourcetype><D:calendar/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 207 Multi-Status</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/caldav/calendars/%(user)s/Familie-Esser/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Familie-Esser</d:displayname>
        <d:resourcetype><D:calendar/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 207 Multi-Status</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/caldav/calendars/%(user)s/work/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Work</d:displayname>
        <d:resourcetype><D:calendar/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 207 Multi-Status</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
""" % {"user": MOCK_USER}

CALENDAR_RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/remote.php/caldav/calendars/%(user)s/Familie-Esser/e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f.ics</d:href>
    <d:propstat>
      <d:prop>
        <c:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f
SUMMARY:Sprint Planning
DTSTART:20261113T100000Z
DTEND:20261113T113000Z
DESCRIPTION:Plan the quarter.
LOCATION:HQ Meeting Room A
END:VEVENT
END:VCALENDAR]]></c:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/caldav/calendars/%(user)s/Familie-Esser/f9c8d7e6-5a4b-3c2d-1e0f-9a8b7c6d5e4f.ics</d:href>
    <d:propstat>
      <d:prop>
        <c:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:f9c8d7e6-5a4b-3c2d-1e0f-9a8b7c6d5e4f
SUMMARY:Lunch with Jan
DTSTART:20261114T120000Z
DTEND:20261114T130000Z
DESCRIPTION:Catch up.
LOCATION:Restaurant
END:VEVENT
END:VCALENDAR]]></c:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/caldav/calendars/%(user)s/Familie-Esser/a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d.ics</d:href>
    <d:propstat>
      <d:prop>
        <c:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d
SUMMARY:Fallback test event
DTSTART:20261115T090000Z
DTEND:20261115T093000Z
END:VEVENT
END:VCALENDAR]]></c:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
""" % {"user": MOCK_USER}

TASKS_RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/remote.php/dav/calendars/%(user)s/tasks</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Tasks</d:displayname>
        <d:resourcetype><C:calendar/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 207 Multi-Status</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
""" % {"user": MOCK_USER}

TASK_LIST_RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/remote.php/dav/calendars/%(user)s/tasks/7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f.ics</d:href>
    <d:propstat>
      <d:prop>
        <c:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VTODO
UID:7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f
SUMMARY:Call the bank
DUE:20261120T180000Z
STATUS:NEEDS-ACTION
END:VTODO
END:VCALENDAR]]></c:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/calendars/%(user)s/tasks/8a7b3d5f-4e0b-5c6d-9f7b-2a3b4c5d6e7f.ics</d:href>
    <d:propstat>
      <d:prop>
        <c:calendar-data><![CDATA[BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VTODO
UID:8a7b3d5f-4e0b-5c6d-9f7b-2a3b4c5d6e7f
SUMMARY:Pay the invoice
DUE:20261118T120000Z
STATUS:COMPLETED
END:VTODO
END:VCALENDAR]]></c:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
""" % {"user": MOCK_USER}

CREATED_RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:"/>
"""


class MockCalDAVHandler(BaseHTTPRequestHandler):
    """Respond to CalDAV operations with fixture XML."""

    protocol_version = "HTTP/1.1"

    # The collections this mock serves; anything else is an unknown calendar.
    KNOWN_CALENDARS = ("JanEsser", "Familie-Esser", "work")

    def log_message(self, _fmt, *args):
        pass  # quiet for test output

    def check_auth(self):
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Basic "):
            return False
        raw = base64.b64decode(auth[6:]).decode("utf-8", "replace")
        user, password = raw.split(":", 1)
        return (user, password) == (MOCK_USER, MOCK_PASSWORD)

    def _calendar_path(self):
        """Path of `/remote.php/caldav/calendars/<user>/<name>/` requests, if any."""
        m = re.match(r"/remote\.php/caldav/calendars/[^/]+/(.+)$", self._path().rstrip("/"))
        return m.group(1) if m else None

    def _is_known_calendar(self):
        """Whether a `/remote.php/caldav/calendars/<user>/<name>/` collection exists.

        Returns `None` for paths that are not CalDAV collections (e.g. the DAV task
        app), leaving their handling to the handlers below unchanged.
        """
        root = f"/remote.php/caldav/calendars/{MOCK_USER}/"
        path = self._path()
        if not path.startswith(root):
            return None
        name = self._calendar_path() or ""
        name = name.strip("/")
        # The collection root always exists; a named calendar only if it is known.
        return not name or name in self.KNOWN_CALENDARS

    def _unknown_calendar(self):
        """Reply with 404 for a collection this mock does not know about."""
        known = self._is_known_calendar()
        if known is None:
            return False
        if known:
            return False
        path = self._path()
        print(f"[caldav-mock] 404 {path}", flush=True)
        self.respond(404, {}, "Not Found")
        return True

    def respond(self, status, headers, body):
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        if "Content-Type" not in headers:
            self.send_header("Content-Type", "application/xml; charset=utf-8")
        if isinstance(body, bytes):
            length = len(body)
        else:
            length = body.encode().__len__()
        self.send_header("Content-Length", str(length))
        self.end_headers()
        if isinstance(body, bytes):
            self.wfile.write(body)
        else:
            self.wfile.write(body.encode())

    def _path(self):
        path = self.path.split("?")[0]
        return path.replace("//", "/") if path.startswith("/") else path

    def do_PROPFIND(self):
        if not self.check_auth():
            self.respond(401, {"WWW-Authenticate": 'Basic realm="cal"'}, "Unauthorized")
            return
        path = self._path()
        print(f"[caldav-mock] PROPFIND {path}", flush=True)
        if self._unknown_calendar():
            return
        if "/remote.php/dav/calendars/" in path and path.endswith("/tasks"):
            body = TASKS_RESPONSE
        else:
            body = LISTING % {"user": MOCK_USER}
        self.respond(207, {}, body)

    def do_REPORT(self):
        if not self.check_auth():
            self.respond(401, {"WWW-Authenticate": 'Basic realm="cal"'}, "Unauthorized")
            return
        path = self._path()
        print(f"[caldav-mock] REPORT {path}", flush=True)
        if self._unknown_calendar():
            return
        if "/tasks" in path:
            body = TASK_LIST_RESPONSE
        else:
            body = CALENDAR_RESPONSE
        self.respond(207, {}, body)

    def do_MKCALENDAR(self):
        if not self.check_auth():
            self.respond(401, {"WWW-Authenticate": 'Basic realm="cal"'}, "Unauthorized")
            return
        path = self._path()
        print(f"[caldav-mock] MKCALENDAR {path}", flush=True)
        self.respond(201, {}, CREATED_RESPONSE)

    def do_PUT(self):
        if not self.check_auth():
            self.respond(401, {"WWW-Authenticate": 'Basic realm="cal"'}, "Unauthorized")
            return
        path = self._path()
        print(f"[caldav-mock] PUT {path}", flush=True)
        if self._unknown_calendar():
            return
        # Default to Nextcloud's 201 Created; MOCK_PUT_STATUS overrides it.
        self.respond(MOCK_PUT_STATUS, {}, CREATED_RESPONSE)

    def do_OPTIONS(self):
        # Allow the extension's discovery of WebDAV capabilities.
        self.send_response(204)
        self.send_header("DAV", "1, 3")
        self.send_header("Content-Length", "0")
        self.end_headers()


class ThreadedServer(ThreadingMixIn, TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self):
        super().server_bind()
        # Expose the port so tests can find it (the port is not yet stored on the
        # instance while `server_bind` runs).
        port = self.socket.getsockname()[1]
        with open(os.path.join(FIXTURES, "mock_port.json"), "w") as f:
            json.dump({"port": port}, f)


_server = None
_thread = None


def start():
    """Start the mock server on a free port. Returns (host, port)."""
    global _server, _thread  # noqa: PLC0103
    stop()
    _server = ThreadedServer(("127.0.0.1", MOCK_PORT), MockCalDAVHandler)
    host, port = _server.server_address
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    return host, port


def stop():
    """Stop the running mock server (no-op if none is running)."""
    global _server, _thread  # noqa: PLC0103
    if _server is not None:
        _server.shutdown()
        _server.server_close()
        _server = None
    if _thread is not None:
        _thread.join(timeout=5)
        _thread = None


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        stop()
        print("stopped")
        return
    start()
    with open(os.path.join(FIXTURES, "mock_port.json"), "w") as f:
        json.dump({"port": _server.server_address[1]}, f)
    print(_server.server_address[1], flush=True)


if __name__ == "__main__":
    main()
