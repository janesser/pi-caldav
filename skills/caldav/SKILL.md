# pi-caldav — Setup & Troubleshooting

## Prerequisites

- An app password for the CalDAV server (see `README.md`).
- A box that can reach the CalDAV server's HTTPS port from outbound HTTPS.
- Python installed on the host running the extension (for future tooling).

## Installation

```bash
pi install git:github.com/janesser/pi-caldav
```

Then set the three environment variables (`PI_CALENDAR_URL`, `PI_CALENDAR_USER`,
`PI_CALENDAR_PASSWORD`) and restart pi-agent, or `/reload` if it is running.

A convenient way to provision them: write these three lines to a file such as
`~/.pi/agent/.env-caldav` and make it readable only by you (chmod 600 it), then source it in
the shell that starts pi-agent before launching it, e.g. `set -a; source ~/.pi/agent/.env-caldav; set +a`:

```bash
PI_CALENDAR_URL=https://<caldav-server-hostname>/
PI_CALENDAR_USER=<username>
PI_CALENDAR_PASSWORD=<app password>
```

The extension is a single file loaded via jiti (`extensions/caldav.ts`). It carries its
own request layer: every tool issues curl calls against the server through
`pi.exec("curl", ...)`, so no client library or daemon is required.

## Tools

| Tool | Purpose |
|---|---|
| `calendar_list_events` | List events, with optional `from`/`to` date bounds |
| `calendar_create_event` | Create an event (`summary`, `start`, `end`, optional `description`/`location`) |
| `task_list` | List VTODO tasks from a task list |

Every tool takes the target calendar as its first parameter — a display name or the full
CalDAV href. A bare name is enumerated at request time (PROPFIND on
`/remote.php/caldav/calendars/<user>/`), so no discovery cache can go stale.

## Troubleshooting

- **401 responses** — check the app password in Nextcloud *Settings → Security*. The
  extension fails with `Calendar collection not accessible: 401 Unauthorized`.
- **No collections found** — run a manual probe (replace `<username>` and the host with your own):
  `curl -sI --basic -u <username>:<app-password> https://<caldav-server-hostname>/remote.php/caldav/calendars/<username>/`
- **404 responses** — the calendar or task list collection does not exist on the server.
- **Unreachable server** — a dead host fails with `WebDAV PROPFIND failed (...)`. The tools
  fail before any payload work instead of crashing.
- **No events parsed** — the calendar's iCal export is empty, or the tool is querying a
  collection that holds no events (`No events found for ...`).

## Testing

The suite runs with no network at all: `python3 -m unittest discover -s tests`.

`tests/mock_caldav_server.py` is a hand-rolled, stdlib-only Nextcloud-style mock (PROPFIND /
REPORT / MKCALENDAR / PUT on `/remote.php/caldav/calendars/<user>/`, tasks under
`/remote.php/dav/calendars/<user>/tasks`). The extension's own curl calls hit it directly
(integration mode), and a node harness (`tests/extension_runner.js`) also exercises the tool
payloads with fixture XML while stubbing `pi.exec` (unit mode).
