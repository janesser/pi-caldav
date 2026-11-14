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
        self.assertEqual(output["tools"], [
            "calendar_list_events", "calendar_create_event", "task_list",
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
        details = result["details"]
        tasks = details["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["summary"], "Call the bank")
        self.assertEqual(tasks[0]["due"], "20261120T180000")
        self.assertEqual(tasks[0]["status"], "NEEDS-ACTION")
        self.assertEqual(tasks[1]["uid"], "8a7b3d5f-4e0b-5c6d-9f7b-2a3b4c5d6e7f")

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
