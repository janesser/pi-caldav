"""Tests for @janesser/pi-caldav.

Starts `tests/mock_caldav_server.py` (a hand-rolled, stdlib-only Nextcloud
mock) on a free port, then exercises `extensions/caldav.ts` through the node
harness `tests/extension_runner.js` in two modes:

  unit   : the exec layer is stubbed with fixture XML (no HTTP at all).
  int    : the extension's own curl call reaches the mock server over HTTP.

No test touches an external network.
"""

import json
import os
import subprocess
import unittest
from glob import glob

# The project root (parent of tests/), used to locate the node harness and the
# typebox module resolved by jiti at load time.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))

# A `node` binary on PATH is required to run the extension via jiti (pi-agent
# injects its own loader at runtime, so this is only for the test suite).
NODE_BIN = (
    os.environ.get("NODE_BIN", "") or next(
        (p for p in glob("/home/jan/.local/share/mise/installs/node/*/bin/node")
         if os.path.isfile(p)),
        "node",
    )
)


def run_tool_unit(tool, params, **kwargs):
    """Run a tool in unit mode with pinned (non-live) credentials.

    Unit mode must not inherit live credentials from the environment; pinning them
    makes request formation assertions independent of this machine. Any additional
    keyword is forwarded to `run_tool` (status, fixture, ...).
    """
    env = dict(os.environ)
    env.update({
        "PI_CALENDAR_URL": "http://127.0.0.1/",
        "PI_CALENDAR_USER": "testuser",
        "PI_CALENDAR_PASSWORD": "testpassword",
    })
    return run_tool("unit", tool, params, env=env, **kwargs)


def run_tool(mode, tool, params, fixture=None, status=207, fail=False, env=None,
             discovery_fixture=None, killed=False, statuses=None):
    """Run a tool and return (output dict, process)."""
    cmd = [NODE_BIN, "--experimental-strip-types", f"{TESTS}/extension_runner.js"]
    cmd += ["--mode=" + mode, f"--tool={tool}", f'--params={json.dumps(params)}']
    if fixture:
        cmd.append(f"--fixture={fixture}")
    if discovery_fixture:
        cmd.append(f"--discovery-fixture={discovery_fixture}")
    if status != 207:
        cmd.append(f"--status={status}")
    if fail:
        cmd.append("--fail")
    if killed:
        cmd.append("--killed")
    if statuses:
        cmd.append(f"--statuses={statuses}")
    proc_env = dict(os.environ) if env is None else dict(env)
    # The extension reads its credentials from process.env; the suite stores them
    # under TEST_PI_* so they can be overridden per test.
    for key in ("TEST_PI_CALENDAR_URL", "TEST_PI_CALENDAR_USER",
                "TEST_PI_CALENDAR_PASSWORD"):
        if key in proc_env:
            # Strip the test-suite prefix so the extension sees its own env names.
            proc_env[key.replace("TEST_PI_", "PI_")] = proc_env[key]
    # The mock server must accept the credentials the extension sends.
    for key in ("MOCK_USER", "MOCK_PASSWORD"):
        if key not in proc_env:
            proc_env[key] = os.environ.get(key, "testuser" if key == "MOCK_USER" else "testpassword")
    proc = subprocess.run(cmd, cwd=ROOT, env=proc_env, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(proc.stdout), proc
    except (json.JSONDecodeError, TypeError):
        raise AssertionError(f"tool output not JSON:\n{proc.stdout}\nstderr:\n{proc.stderr}")


class TestCalDAV(unittest.TestCase):
    """Unit tests (stubbed exec) + integration tests (mock server)."""

    @classmethod
    def setUpClass(cls):
        from mock_caldav_server import start  # noqa: PLC0415
        cls.host, cls.port = start()
        cls.url = f"http://127.0.0.1:{cls.port}/"
        cls.base_env = dict(os.environ)
        cls.base_env.update(
            {
                "TEST_PI_CALENDAR_URL": cls.url,
                "TEST_PI_CALENDAR_USER": "testuser",
                "TEST_PI_CALENDAR_PASSWORD": "testpassword",
                # Match what the mock server accepts for basic auth.
                "MOCK_USER": "testuser",
                "MOCK_PASSWORD": "testpassword",
            }
        )

    @classmethod
    def tearDownClass(cls):
        from mock_caldav_server import stop  # noqa: PLC0415
        stop()

    def test_tool_registration(self):
        """The extension registers the three tools with typebox params."""
        # `tools` mode loads the extension and prints the registered tool names
        # without executing anything, so no request is made.
        proc = subprocess.run(
            [NODE_BIN, "--experimental-strip-types", f"{TESTS}/extension_runner.js", "--mode=tools"],
            cwd=ROOT, env=dict(os.environ), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0)
        output = json.loads(proc.stdout)
        # Write tools land after the read tools, in registration order.
        # Read tools first, then the task write family, then the event write family.
        self.assertEqual(output["tools"], [
            "calendar_list_events", "calendar_create_event", "task_list",
            "task_create", "task_update_status", "task_delete",
            "event_update", "event_delete",
        ])

    def test_list_events_unit(self):
        """A stubbed REPORT with fixture XML parses into events."""
        output, _ = run_tool(
            "unit",
            "calendar_list_events",
            {"calendar": "JanEsser"},
            fixture="report_events.xml",
        )
        result = output["result"]
        content = result["content"][0]["text"]
        self.assertIn("Events for JanEsser (2 found)", content)
        self.assertIn("Sprint Planning", content)
        self.assertIn("Lunch with Jan", content)
        details = result["details"]
        self.assertEqual(details["count"], 2)
        events = details["events"]
        self.assertEqual(events[0]["summary"], "Sprint Planning")
        self.assertEqual(events[0]["start"], "20261113T100000")
        self.assertEqual(events[0]["end"], "20261113T113000")
        self.assertEqual(events[0]["uid"], "e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f")
        self.assertEqual(events[0]["description"], "Plan the quarter.")
        self.assertEqual(events[0]["location"], "HQ Meeting Room A")
        self.assertIn("raw_ical", details)

    def test_list_events_request_formatting(self):
        """The stubbed exec records the request the extension would send."""
        output, _ = run_tool(
            "unit",
            "calendar_list_events",
            {"calendar": "JanEsser"},
            fixture="report_events.xml",
        )
        calls = output["execCalls"]
        self.assertEqual(len(calls), 2)  # discovery PROPFIND + REPORT
        self.assertEqual(calls[0]["command"], "curl")
        self.assertIn("-X", calls[0]["args"])
        self.assertIn("PROPFIND", calls[0]["args"])
        self.assertIn("--basic", calls[0]["args"])
        self.assertIn("-u", calls[0]["args"])
        self.assertIn("-D", calls[0]["args"])
        self.assertEqual(calls[1]["command"], "curl")
        self.assertIn("REPORT", calls[1]["args"])

    def test_list_events_no_events(self):
        """An empty REPORT lists no events instead of crashing."""
        output, _ = run_tool(
            "unit",
            "calendar_list_events",
            {"calendar": "Familie-Esser"},
            fixture="report_empty.xml",  # PROPFIND listing only
        )
        self.assertIn("No events found for Familie-Esser", output["result"]["content"][0]["text"])

    def test_create_event_unit(self):
        """calendar_create_event builds and sends a VEVENT."""
        output, _ = run_tool(
            "unit",
            "calendar_create_event",
            {"calendar": "JanEsser", "summary": "Meeting",
             "start": "20261113T100000Z", "end": "20261113T110000Z"},
            status=201,  # A successful PUT on a Nextcloud CalDAV calendar.
        )
        self.assertIn("Event created on JanEsser", output["result"]["content"][0]["text"])
        # The stub emulates the mock's real 201 response; see `test_create_event_int`.
        self.assertEqual(output["result"]["details"]["status"], 201)
        # `calendar_create_event` resolves the calendar first, so the PUT is the
        # second exec call (the discovery PROPFIND comes first).
        calls = output["execCalls"]
        put = next((c for c in calls if "PUT" in c["args"]))
        self.assertEqual(put["command"], "curl")
        self.assertIn("PUT", put["args"])
        self.assertIn("--data-raw", put["args"])
        body_arg = put["args"][put["args"].index("--data-raw") + 1]
        self.assertIn("BEGIN:VEVENT", body_arg)
        self.assertIn("SUMMARY:Meeting", body_arg)

    def test_task_create_unit(self):
        """task_create builds and sends a VTODO to the task list collection."""
        output, _ = run_tool_unit(
            "task_create",
            {
                "calendar": "JanEsser",
                "summary": "Call the bank",
                "due": "20261120T180000Z",
                "description": "Urgent.",
            },
            status=201,  # The mock's real PUT response (Nextcloud: 201 Created).
        )
        result = output["result"]
        self.assertIn("Task created on JanEsser", result["content"][0]["text"])
        self.assertEqual(result["details"]["status"], 201)
        # A single PUT carries the whole VTODO; no discovery round trip is needed.
        calls = output["execCalls"]
        self.assertEqual(len(calls), 1)
        put = next((c for c in calls if "PUT" in c["args"]))
        self.assertEqual(put["command"], "curl")
        self.assertIn("--data-raw", put["args"])
        body_arg = put["args"][put["args"].index("--data-raw") + 1]
        self.assertIn("BEGIN:VTODO", body_arg)
        self.assertIn("SUMMARY:Call the bank", body_arg)
        self.assertIn("DUE:20261120T180000Z", body_arg)
        self.assertIn("DESCRIPTION:Urgent.", body_arg)
        # The URL is the argument just before `--data-raw`; the body carries the VTODO.
        # Only the path part of the URL is asserted — it must target the task list
        # collection, which the host prefix can never swallow.
        url_arg = put["args"][put["args"].index("--data-raw") - 1]
        self.assertIn(
            "remote.php/dav/calendars/testuser/tasks/",
            url_arg.replace("https://", "").replace("http://", ""),
        )

    def test_task_update_status_unit(self):
        """A status update reads, patches and PUTs the VTODO body (no data loss)."""
        output, _ = run_tool_unit(
            "task_update_status",
            {
                "href": "/remote.php/dav/calendars/testuser/tasks/7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f.ics",
                "status": "COMPLETED",
            },
            # The first call is a PROPFIND read (`--discovery-fixture` covers any
            # PROPFIND, including resource reads); the second is the PUT (201 Created).
            discovery_fixture="task_resource.xml",
            status=201,
        )
        result = output["result"]
        self.assertIn("status updated to COMPLETED", result["content"][0]["text"])
        calls = output["execCalls"]
        self.assertEqual(len(calls), 2)
        # The read comes first, then the PUT (with the patched body).
        self.assertIn("PROPFIND", calls[0]["args"])
        self.assertEqual(
            calls[0]["args"][calls[0]["args"].index("-X") + 1], "PROPFIND"
        )
        put = next((c for c in calls if "PUT" in c["args"]))
        body_arg = put["args"][put["args"].index("--data-raw") + 1]
        self.assertIn("BEGIN:VTODO", body_arg)
        # The original properties survive the patch; only STATUS is changed.
        self.assertIn("UID:7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f", body_arg)
        self.assertIn("SUMMARY:Call the bank", body_arg)
        self.assertIn("DUE:20261120T180000Z", body_arg)
        self.assertIn("STATUS:COMPLETED", body_arg)

    def test_task_update_status_uid_unit(self):
        """An href-less update builds the resource URL from list and UID."""
        output, _ = run_tool_unit(
            "task_update_status",
            {
                "calendar": "/remote.php/dav/calendars/testuser/tasks",
                "uid": "7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f", "status": "COMPLETED",
            },
            discovery_fixture="task_resource.xml",
            status=201,  # The second call is the PUT (201 Created).
        )
        self.assertIn("status updated to COMPLETED", output["result"]["content"][0]["text"])
        put = next((c for c in output["execCalls"] if "PUT" in c["args"]))
        body_arg = put["args"][put["args"].index("--data-raw") + 1]
        self.assertIn("STATUS:COMPLETED", body_arg)

    def test_task_update_status_not_found_unit(self):
        """An update of a missing task surfaces a clear error, not an exception."""
        output, _ = run_tool_unit(
            "task_update_status",
            {
                "href": "/remote.php/dav/calendars/testuser/tasks/missing.ics", "status": "COMPLETED",
            },
            # No calendar-data in the response means the task is missing, not
            # malformed: the extension surfaces a clear error instead of an exception.
            discovery_fixture="report_empty.xml",
        )
        self.assertIn("error", output)
        self.assertIn("not found", output["error"])

    def test_task_update_status_int(self):
        """The status update's PUT reaches the mock server with a patched VTODO body."""
        output, proc = run_tool(
            "int",
            "task_update_status",
            {"href": "/remote.php/dav/calendars/testuser/tasks/7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f.ics", "status": "COMPLETED"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("status updated to COMPLETED", output["result"]["content"][0]["text"])
        put = next((c for c in output["execCalls"] if "PUT" in c["args"]))
        body_arg = put["args"][put["args"].index("--data-raw") + 1]
        self.assertIn("BEGIN:VTODO", body_arg)
        # The original properties survive the patch; only STATUS is changed.
        self.assertIn("SUMMARY:Call the bank", body_arg)
        self.assertIn("DUE:20261120T180000Z", body_arg)
        self.assertIn("STATUS:COMPLETED", body_arg)

    def test_task_update_status_not_found_int(self):
        """A PUT on a missing task surfaces a clear error, not an exception."""
        output, proc = run_tool(
            "int",
            "task_update_status",
            {"href": "/remote.php/dav/calendars/testuser/tasks/missing.ics", "status": "COMPLETED"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("error", output)
        self.assertIn("not found", output["error"])

    def test_task_update_status_missing_href_unit(self):
        """A status update without href, calendar and uid fails with a clear error."""
        output, _ = run_tool_unit(
            "task_update_status", {"status": "COMPLETED"},
        )
        self.assertIn("error", output)
        self.assertIn("requires an href", output["error"])

    def test_task_update_status_missing_status_unit(self):
        """A status update without a status fails with a clear error, not a TypeError."""
        output, _ = run_tool_unit(
            "task_update_status", {"href": "/remote.php/dav/calendars/testuser/tasks/x.ics"},
        )
        self.assertIn("error", output)
        self.assertIn("requires a status", output["error"])

    def test_task_delete_unit(self):
        # The mock replies 204 No Content on a successful delete.
        output, _ = run_tool_unit(
            "task_delete",
            {"href": "/remote.php/dav/calendars/testuser/tasks/7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f.ics"},
            status=204,
        )
        self.assertIn("deleted", output["result"]["content"][0]["text"])
        calls = output["execCalls"]
        self.assertEqual(len(calls), 1)
        delete = next((c for c in calls if "DELETE" in c["args"]))
        self.assertEqual(delete["args"][delete["args"].index("-X") + 1], "DELETE")
        # The DELETE lands on the task's resource URL, not a calendar collection.
        self.assertIn(
            "remote.php/dav/calendars/testuser/tasks/",
            delete["args"][-1].replace("https://", "").replace("http://", ""),
        )

    def test_task_delete_uid_unit(self):
        """A delete without an href builds the resource URL from list and UID."""
        output, _ = run_tool_unit(
            "task_delete",
            {"calendar": "/remote.php/dav/calendars/testuser/tasks", "uid": "7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f"},
            status=204,  # The mock replies 204 No Content on a successful delete.
        )
        self.assertIn("deleted", output["result"]["content"][0]["text"])
        delete = next((c for c in output["execCalls"] if "DELETE" in c["args"]))
        # The URL is the last argument (there is no body on a DELETE).
        url_arg = delete["args"][-1]
        self.assertIn(
            "remote.php/dav/calendars/testuser/tasks/7f6a2c4e",
            url_arg.replace("https://", "").replace("http://", ""),
        )

    def test_task_delete_int(self):
        """A delete's DELETE reaches the mock server on the task resource."""
        output, proc = run_tool(
            "int",
            "task_delete",
            {"href": "/remote.php/dav/calendars/testuser/tasks/7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f.ics"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("deleted", output["result"]["content"][0]["text"])
        delete = next((c for c in output["execCalls"] if "DELETE" in c["args"]))
        self.assertEqual(delete["args"][delete["args"].index("-X") + 1], "DELETE")
        self.assertIn(
            "remote.php/dav/calendars/testuser/tasks/",
            delete["args"][-1].replace("https://", "").replace("http://", ""),
        )

    def test_task_delete_missing_href_unit(self):
        """A delete without href, calendar and uid fails with a clear error."""
        output, _ = run_tool_unit(
            "task_delete", {"calendar": "/remote.php/dav/calendars/testuser/tasks"},
        )
        self.assertIn("error", output)
        self.assertIn("requires an href", output["error"])

    def test_event_update_unit(self):
        """An event update reads, patches and PUTs the VEVENT body (no data loss)."""
        output, _ = run_tool_unit(
            "event_update",
            {"href": "/remote.php/caldav/calendars/testuser/Familie-Esser/e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f.ics", "summary": "Sprint Planning (rescheduled)"},
            # The first call is a PROPFIND read (`--discovery-fixture` covers any
            # PROPFIND, including resource reads); the second is the PUT (201 Created).
            discovery_fixture="event_resource.xml",
            status=201,
        )
        result = output["result"]
        self.assertIn(
            "Event e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f updated",
            result["content"][0]["text"],
        )
        calls = output["execCalls"]
        self.assertEqual(len(calls), 2)
        # The read comes first, then the PUT (with the patched body).
        self.assertIn("PROPFIND", calls[0]["args"])
        put = next((c for c in calls if "PUT" in c["args"]))
        body_arg = put["args"][put["args"].index("--data-raw") + 1]
        self.assertIn("BEGIN:VEVENT", body_arg)
        # The original properties survive the patch; only SUMMARY is changed.
        self.assertIn("UID:e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f", body_arg)
        self.assertIn("SUMMARY:Sprint Planning (rescheduled)", body_arg)
        self.assertIn("DTSTART:20261113T100000Z", body_arg)
        self.assertIn("DTEND:20261113T113000Z", body_arg)
        self.assertIn("DESCRIPTION:Plan the quarter.", body_arg)
        self.assertIn("LOCATION:HQ Meeting Room A", body_arg)

    def test_event_update_field_unit(self):
        """An event update only re-sends the fields that are passed."""
        output, _ = run_tool_unit(
            "event_update",
            {
                "calendar": "/remote.php/caldav/calendars/testuser/Familie-Esser",
                "uid": "e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f",
                "start": "20261114T100000Z", "end": "20261114T113000Z",
            },
            discovery_fixture="event_resource.xml",
            status=201,  # The second call is the PUT (201 Created).
        )
        self.assertIn(
            "Event e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f updated",
            output["result"]["content"][0]["text"],
        )
        put = next((c for c in output["execCalls"] if "PUT" in c["args"]))
        body_arg = put["args"][put["args"].index("--data-raw") + 1]
        self.assertIn("DTSTART:20261114T100000Z", body_arg)
        self.assertIn("DTEND:20261114T113000Z", body_arg)
        # untouched fields come back from the server.
        self.assertIn("SUMMARY:Sprint Planning", body_arg)

    def test_event_update_not_found_unit(self):
        """An update of a missing event surfaces a clear error, not an exception."""
        output, _ = run_tool_unit(
            "event_update",
            {"href": "/remote.php/caldav/calendars/testuser/Familie-Esser/missing.ics"},
            # No calendar-data in the response means the event is missing.
            discovery_fixture="report_empty.xml",
        )
        self.assertIn("error", output)
        self.assertIn("not found", output["error"])

    def test_event_update_missing_href_unit(self):
        """An update without href, calendar and uid fails with a clear error."""
        output, _ = run_tool_unit(
            "event_update", {"start": "20261114T100000Z"},
        )
        self.assertIn("error", output)
        self.assertIn("requires an href", output["error"])

    def test_event_delete_unit(self):
        """An event deletion sends a DELETE to the event's resource URL."""
        output, _ = run_tool_unit(
            "event_delete",
            {"href": "/remote.php/caldav/calendars/testuser/Familie-Esser/e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f.ics"},
            status=204,  # The mock replies 204 No Content on a successful delete.
        )
        self.assertIn("deleted", output["result"]["content"][0]["text"])
        calls = output["execCalls"]
        self.assertEqual(len(calls), 1)
        delete = next((c for c in calls if "DELETE" in c["args"]))
        self.assertEqual(delete["args"][delete["args"].index("-X") + 1], "DELETE")
        # The DELETE lands on the event's resource URL, not a calendar collection.
        self.assertIn(
            "remote.php/caldav/calendars/testuser/Familie-Esser/",
            delete["args"][-1].replace("https://", "").replace("http://", ""),
        )

    def test_event_delete_uid_unit(self):
        """A delete without an href builds the resource URL from calendar and UID."""
        output, _ = run_tool_unit(
            "event_delete",
            {"calendar": "/remote.php/caldav/calendars/testuser/Familie-Esser", "uid": "e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f"},
            status=204,  # The mock replies 204 No Content on a successful delete.
        )
        self.assertIn("deleted", output["result"]["content"][0]["text"])
        delete = next((c for c in output["execCalls"] if "DELETE" in c["args"]))
        url_arg = delete["args"][-1]
        self.assertIn(
            "remote.php/caldav/calendars/testuser/Familie-Esser/e47bb0c9",
            url_arg.replace("https://", "").replace("http://", ""),
        )

    def test_event_delete_not_found_int(self):
        """A delete of a missing event surfaces a clear error, not an exception."""
        output, proc = run_tool(
            "int",
            "event_delete",
            {"href": "/remote.php/caldav/calendars/testuser/Familie-Esser/missing.ics"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("error", output)
        self.assertIn("not found", output["error"])

    def test_event_update_int(self):
        """The event update's PUT reaches the mock server with a patched VEVENT body."""
        output, proc = run_tool(
            "int",
            "event_update",
            {"href": "/remote.php/caldav/calendars/testuser/Familie-Esser/e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f.ics", "status": "CONFIRMED"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn(
            "Event e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f updated",
            output["result"]["content"][0]["text"],
        )
        put = next((c for c in output["execCalls"] if "PUT" in c["args"]))
        body_arg = put["args"][put["args"].index("--data-raw") + 1]
        self.assertIn("BEGIN:VEVENT", body_arg)
        # The original properties survive the patch; only STATUS is changed.
        self.assertIn("SUMMARY:Sprint Planning", body_arg)
        self.assertIn("DTSTART:20261113T100000Z", body_arg)
        self.assertIn("STATUS:CONFIRMED", body_arg)

    def test_event_delete_int(self):
        """An event delete's DELETE reaches the mock server on the event resource."""
        output, proc = run_tool(
            "int",
            "event_delete",
            {"href": "/remote.php/caldav/calendars/testuser/Familie-Esser/e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f.ics"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("deleted", output["result"]["content"][0]["text"])
        delete = next((c for c in output["execCalls"] if "DELETE" in c["args"]))
        self.assertEqual(delete["args"][delete["args"].index("-X") + 1], "DELETE")

    def test_task_delete_not_found_int(self):
        """A delete of a missing task surfaces a clear error, not an exception."""
        output, proc = run_tool(
            "int",
            "task_delete",
            {"href": "/remote.php/dav/calendars/testuser/tasks/missing.ics"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("error", output)
        self.assertIn("not found", output["error"])

    def test_task_create_missing_summary_unit(self):
        """A create call without a summary fails with a clear error, not a TypeError."""
        output, _ = run_tool_unit(
            "task_create",
            {"calendar": "JanEsser", "due": "20261120T180000Z"},
        )
        self.assertIn("error", output)
        self.assertIn("requires a summary", output["error"])

    def test_task_create_invalid_href_unit(self):
        """An explicit task-list href is used as-is by task_create."""
        output, _ = run_tool_unit(
            "task_create",
            {"calendar": "/remote.php/dav/calendars/testuser/tasks", "summary": "Call the bank"},
            status=201,
        )
        result = output["result"]
        self.assertIn("Task created on /remote.php/dav/calendars/testuser/tasks",
                      result["content"][0]["text"])
        url_arg = output["execCalls"][0]["args"][output["execCalls"][0]["args"].index("--data-raw") - 1]
        self.assertIn(
            "remote.php/dav/calendars/testuser/tasks/",
            url_arg.replace("https://", "").replace("http://", ""),
        )

    def test_task_create_int(self):
        """The extension's PUT reaches the mock server with a VTODO body."""
        output, proc = run_tool(
            "int",
            "task_create",
            {"calendar": "JanEsser", "summary": "Call the bank"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Task created on JanEsser", output["result"]["content"][0]["text"])
        calls = output["execCalls"]
        put = next((c for c in calls if "PUT" in c["args"]))
        body_arg = put["args"][put["args"].index("--data-raw") + 1]
        self.assertIn("BEGIN:VTODO", body_arg)
        self.assertIn("SUMMARY:Call the bank", body_arg)

    def test_task_list_unit(self):
        """A stubbed task-list REPORT parses VTODOs."""
        output, _ = run_tool(
            "unit",
            "task_list",
            {"calendar": "/remote.php/dav/calendars/testuser/tasks"},
            fixture="report_tasks.xml",
            # The task list tool first PROPFINDs the collection it is given.
            discovery_fixture="propfind_tasks.xml",
        )
        result = output["result"]
        self.assertIn("Tasks for /remote.php/dav/calendars/testuser/tasks (2 found)",
                      result["content"][0]["text"])
        # Every task reports its resource href, so writes can target it directly.
        self.assertIn(
            "/remote.php/dav/calendars/testuser/tasks/7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f.ics",
            result["content"][0]["text"],
        )
        details = result["details"]
        tasks = details["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["summary"], "Call the bank")
        self.assertEqual(tasks[0]["due"], "20261120T180000")
        self.assertEqual(tasks[0]["status"], "NEEDS-ACTION")
        self.assertEqual(tasks[1]["uid"], "8a7b3d5f-4e0b-5c6d-9f7b-2a3b4c5d6e7f")
        # The task list reports each task's resource href so writes can target it.
        self.assertEqual(
            tasks[0]["href"],
            "/remote.php/dav/calendars/testuser/tasks/7f6a2c4e-3e9a-4c5b-8f6a-1a2b3c4d5e6f.ics",
        )

    def test_timeout_unit(self):
        """A timed out exec surfaces a clear timeout error, not an exception."""
        output, _ = run_tool(
            "unit", "calendar_list_events", {"calendar": "JanEsser"}, killed=True
        )
        self.assertIn("error", output)
        self.assertIn("timed out", output["error"])

    def test_create_event_non2xx_unit(self):
        """A 500 on the create PUT surfaces as a clear error, not an exception."""
        # The stub cycles through per-call statuses: discovery gets 207, the PUT gets 500.
        output, _ = run_tool(
            "unit",
            "calendar_create_event",
            {"calendar": "JanEsser", "summary": "Meeting",
             "start": "20261113T100000Z", "end": "20261113T110000Z"},
            statuses="207,500",
        )
        self.assertIn("error", output)
        # Only discovery ran (the PUT failed before any event was created).
        calls = output["execCalls"]
        self.assertEqual(len(calls), 2)
        put = next((c for c in calls if "PUT" in c["args"]))
        self.assertIn("WebDAV PUT failed", output["error"])

    def test_blank_calendar_unit(self):
        """A missing calendar fails with a clear error instead of resolving silently."""
        output, _ = run_tool(
            "unit", "calendar_list_events", {"calendar": ""},
        )
        self.assertIn("error", output)
        self.assertIn("calendar is required", output["error"])

    def test_create_event_missing_dates_unit(self):
        """A create call without start/end fails with a clear error, not a TypeError."""
        output, _ = run_tool(
            "unit",
            "calendar_create_event",
            {"calendar": "JanEsser", "summary": "Meeting"},
        )
        self.assertIn("error", output)
        self.assertIn("requires a start and an end date", output["error"])

    def test_401_unit(self):
        """Authentication failure surfaces as a clear error, not an exception."""
        output, _ = run_tool("unit", "calendar_list_events", {"calendar": "JanEsser"}, status=401)
        self.assertIn("error", output)
        self.assertIn("401 Unauthorized", output["error"])

    def test_curl_failure_unit(self):
        """A failed exec (non-zero exit code) surfaces as a clear error."""
        output, _ = run_tool("unit", "calendar_list_events", {"calendar": "JanEsser"}, fail=True)
        self.assertIn("error", output)
        self.assertIn("failed", output["error"])

    def test_list_events_int(self):
        """The extension parses a real HTTP response from the mock server."""
        output, proc = run_tool(
            "int",
            "calendar_list_events",
            {"calendar": "JanEsser"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        result = output["result"]
        content = result["content"][0]["text"]
        self.assertIn("Sprint Planning", content)
        self.assertIn("Lunch with Jan", content)
        # Every event reports its resource href, so writes can target it directly.
        self.assertIn(
            "/remote.php/caldav/calendars/testuser/Familie-Esser/e47bb0c9-1a2b-3c4d-5e6f-7a8b9c0d1e2f.ics",
            content,
        )
        # The mock's fixture enumerates three events for the calendar.
        self.assertEqual(result["details"]["count"], 3)

    def test_list_events_by_name_int(self):
        """Discovery at request time resolves a calendar name to its href."""
        output, proc = run_tool(
            "int",
            "calendar_list_events",
            {"calendar": "Familie-Esser"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Sprint Planning", output["result"]["content"][0]["text"])

    def test_create_event_int(self):
        """The extension's PUT reaches the mock server with a VEVENT body."""
        output, proc = run_tool(
            "int",
            "calendar_create_event",
            {"calendar": "/remote.php/caldav/calendars/testuser/Familie-Esser/",
             "summary": "Meeting", "start": "20261113T100000Z",
             "end": "20261113T110000Z"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Event created on /remote.php/caldav/calendars/testuser/Familie-Esser/",
                      output["result"]["content"][0]["text"])

    def test_task_list_int(self):
        """Task lists under /remote.php/dav/calendars/<user>/tasks are parsed."""
        output, proc = run_tool(
            "int",
            "task_list",
            {"calendar": "/remote.php/dav/calendars/testuser/tasks"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Call the bank", output["result"]["content"][0]["text"])
        self.assertIn("Pay the invoice", output["result"]["content"][0]["text"])

    def test_task_list_by_name_int(self):
        """A bare user name resolves to its task list collection."""
        output, proc = run_tool(
            "int",
            "task_list",
            {"calendar": "testuser"},
            env=self.base_env,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Call the bank", output["result"]["content"][0]["text"])

    def test_401_int(self):
        """Authentication failures against the mock produce a clear error."""
        env = dict(self.base_env)
        env["TEST_PI_CALENDAR_PASSWORD"] = "wrong-password"
        output, proc = run_tool(
            "int", "calendar_list_events", {"calendar": "JanEsser"}, env=env
        )
        self.assertIn("error", output)
        self.assertIn("401 Unauthorized", output["error"])

    def test_404_int(self):
        """Unknown collections produce a clear 404 error message."""
        env = dict(self.base_env)
        env["TEST_PI_CALENDAR_URL"] = f"http://127.0.0.1:{self.port}/"
        # A path the mock does not know how to serve returns an unknown status,
        # which the extension reports as a failed WebDAV request.
        output, proc = run_tool(
            "int",
            "calendar_list_events",
            {"calendar": "/remote.php/caldav/calendars/testuser/Familie-Esser/unknown-calendar/"},
            env=env,
        )
        self.assertIn("error", output)

    def test_unreachable_int(self):
        """A dead server produces a clear error, not a crash."""
        env = dict(self.base_env)
        env["TEST_PI_CALENDAR_URL"] = "http://127.0.0.1:1/"  # nothing listens on port 1
        output, proc = run_tool("int", "calendar_list_events", {"calendar": "JanEsser"}, env=env)
        self.assertIn("error", output)
        self.assertIn("WebDAV", output["error"])

    def test_discovery_is_at_request_time(self):
        """Discovery enumerates collections on every call, never from cache."""
        # Two calls with different calendars must both reach the extension (no
        # module-level caching in extensions/caldav.ts).
        for calendar in ("JanEsser", "Familie-Esser"):
            output, _ = run_tool(
                "unit", "calendar_list_events", {"calendar": calendar},
                fixture="report_events.xml",
            )
            self.assertIn("content", output["result"])


if __name__ == "__main__":
    unittest.main()
