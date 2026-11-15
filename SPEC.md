# SPEC.md — CalDAV Write Operations

## Goal

Let pi **write** to a CalDAV server, not just read it: create tasks, update task status (e.g. mark one done), delete tasks and events, and update/delete calendar events.

## Scope

The `@janesser/pi-caldav` package (installed via `pi install git:github.com/janesser/pi-caldav`) exposes the tools below. The extension is a single curl-driven WebDAV client loaded via jiti (`extensions/caldav.ts`); it reads credentials from `~/.pi/agent/.env-caldav` at load time, with env vars taking precedence.

## Tool inventory (planned)

| Tool | Action | Parameters |
|---|---|---|
| `task_create` | create a VTODO in a task list | `calendar`, `summary`, `due?`, `description?` |
| `task_update_status` | update a task's STATUS property | `href`, or `uid` + `calendar`, `status` |
| `task_delete` | delete a task resource | `href` or `uid` + `calendar` |
| `event_update` | rewrite an event (VEVENT) | `href`, `summary?`, `start?`, `end?`, ... |
| `event_delete` | delete an event resource | `href` or `uid` + `calendar` |

## Decisions

- Tool names are discoverable verbs, not a single `task_edit`. The LLM needs the name to find the capability.
- Task/event identification for updates/deletes uses the **resource href** (e.g. `/remote.php/dav/calendars/<user>/tasks/<uid>.ics`) returned by read tools — one PUT/DELETE, no discovery round trips. `task_update_status` accepts either the full resource href or the UID + calendar when only the list is known.
- Every write tool reuses the existing WebDAV client (`webdav`) and the mock-server test strategy.

## Test strategy (unchanged)

`python3 -m unittest discover -s tests` — no network:
- **unit mode:** `pi.exec` stubbed, fixture XML supplied via `--fixture`/`--discovery-fixture`; verifies request formation and payload parsing.
- **int mode:** real curl calls reach `tests/mock_caldav_server.py`, a hand-rolled stdlib-only Nextcloud-style mock on a free port (already serves PROPFIND/REPORT/MKCALENDAR/PUT).

## Status

| # | Tool | Status |
|---|---|---|
| 1 | `task_create` | **done** — PUT a VTODO to the task list collection |
| 2 | `task_update_status` | **done** — reads the current iCal body, patches `STATUS` and re-PUTs it back so no other property is lost (the read uses PROPFIND with `calendar-data`). Also accepts `uid` + `calendar` in addition to `href`, which is how a task found by `task_list` gets identified |
| 3 | `task_delete` | **done** — DELETE the task resource |
| 4 | `event_update` / `event_delete` | **done** — same read-patch-write + DELETE pattern over VEVENTs |

## Full inventory

Read: `calendar_list_events`, `calendar_create_event`, `task_list`
Write: `task_create`, `task_update_status`, `task_delete`, `event_update`, `event_delete`

## Increments

## Acceptance (for every write tool)

- Registers with `pi.registerTool()` and appears in the tools list (`--mode=tools`).
- Unit test passes: stubbed exec, request formation verified, error paths surfaced (401/404/500).
- Integration test passes: real PUT against the mock server; body contains `BEGIN:VTODO` / `BEGIN:VEVENT`.
