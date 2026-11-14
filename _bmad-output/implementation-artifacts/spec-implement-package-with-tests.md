---
title: "Implement @janesser/pi-caldav: WebDAV client, event/task tools, tests"
type: 'feature'
created: '2026-11-13'
status: 'ready-for-dev'
route: 'dispatch'
baseline_commit: 'NO_VCS'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

The `@janesser/pi-caldav` package is scaffolding only: tools are registered but return "pending" text, and the request layer (`webdav()`) parses nothing. Implement the real WebDAV client (PROPFIND discovery, REPORT event queries, MKCALENDAR/PUT), the event/task tool payloads, and the test harness including a CalDAV mock, so the agent can read and write calendars end to end.

**Problem:** The extension registers `calendar_list_events`, `calendar_create_event`, `task_list` but returns placeholder text — no request layer, no payload parsing, no tests.

**Approach:** A curl-driven WebDAV client in `extensions/caldav.ts` returning structured results, with unit tests that stub the exec layer and integration tests against a hand-rolled Python HTTP mock serving Nextcloud-style `/remote.php/caldav/calendars/` paths.

## Boundaries & Constraints

**Always:** Follow the pi-signal convention — `export default function (pi: ExtensionAPI)` factory, `Type.Object()` parameters via `typebox`, `{ content: [{ type: "text", text }], details: {} }` return shape, throw on failure. Credentials come from `process.env` (`PI_CALENDAR_URL`, `PI_CALENDAR_USER`, `PI_CALENDAR_PASSWORD`) — never committed. One extension file only; no build step (jiti loads `extensions/*.ts`).

**Never:** A persistent daemon, a Python client dependency, or any change to `~/.pi/agent/settings.json` packages (the user wires that in). No XML parsing with regex-only heuristics — use an XML parser.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| `calendar_list_events` happy path | `calendar` = calendar name/href, optional `from`/`to` | Parsed events: summary, start, end, uid; details includes raw iCal and href | 401/404 → error message naming the collection; timeout → `killed` report |
| `calendar_create_event` happy path | `calendar`, `summary`, `start`, `end` (+ optional description/location) | PUT of a VEVENT to `/remote.php/caldav/calendars/<user>/<cal>/`; 201/204 confirmed | Non-2xx → error with body; auth failure → clear message |
| `task_list` happy path | `calendar` = task-list collection href | Parsed VTODOs: summary, due, status, uid | Same as list errors |
| Discovery | Server URL reachable | All collections enumerated at request time | Unreachable/401 → error before tool payload work |

## Decisions

- TOOL_NAMES — Keep `calendar_list_events` / `calendar_create_event` / `task_list`. Matches README/docs; no rename.
- DISCOVERY — Enumerate collections at request time (fresh each call). More robust than session caching.
- TASK_ENDPOINT — Tasks live under `/remote.php/dav/calendars/<user>/tasks`. The mock serves this path so the client validates it before auth is provisioned.

</frozen-after-approval>

<!-- All Open Questions answered above inside <frozen-after-approval>; section deleted. -->

## Code Map

- `extensions/caldav.ts` -- single extension file to implement: `webdav()` client + 3 tools + discovery
- `~/.pi/agent/npm/node_modules/@aalzubidy/pi-signal/extensions/signal.ts` -- reference implementation convention (tool registration, curl exec, env-var creds)
- `@earendil-works/pi-coding-agent/dist/core/exec.d.ts` -- `pi.exec(command, args, options?)` → `{ stdout, stderr, code, killed }`, `ExecOptions { timeout?, signal?, cwd? }`
- `~/.pi/agent/npm/node_modules/@earendil-works/pi-coding-agent/docs/extensions.md` -- extension API docs (registerTool, execute semantics, error by throwing)
- `package.json` (`pi.extensions`) + `skills/caldav/SKILL.md` -- entry point and setup/troubleshooting skill to update
- `~/.pi/agent/settings.json` — packages list; user wires this package in after build

## Tasks & Acceptance

**Execution:**
- [x] `extensions/caldav.ts` -- implement WebDAV client: PROPFIND collection discovery, REPORT event query with time-range filter, MKCALENDAR/PUT for events, basic-auth headers, error parsing, response body/headers return
- [x] `extensions/caldav.ts` -- implement tool payloads: parse VEVENT/VTODO fields into event list, parse VTODO into task list, handle 401/404/timeout errors
- [x] `tests/mock_caldav_server.py` -- hand-rolled Python HTTP mock on a free port; Nextcloud-style `/remote.php/caldav/calendars/` and `/remote.php/dav/calendars/<user>/tasks` paths; fixture XML for PROPFIND/REPORT/MKCALENDAR/PUT responses
- [x] `tests/extension_runner.js` -- node harness in `unit`, `int` and `tools` modes; `--statuses=` cycles per-call statuses (discovery 207, PUT error)
- [x] `tests/test_caldav.py` -- 19 unittest tests (stubbed-exec unit + real-HTTP integration), no network
- [x] `skills/caldav/SKILL.md`, README, handover -- update with implemented state and test strategy

**Acceptance Criteria:**
- Given a CalDAV-compatible URL, when `calendar_list_events` runs, then it returns real event summaries parsed from iCal via the HTTP layer
- Given fixture XML and a mock server, when the integration suite runs, all tests pass without touching the network
- Given unauthenticated or unreachable state, when any tool runs, then it fails with a clear error message (not an exception)

## Verification

**Commands:**
- `python3 -m unittest discover -s tests` -- mock server starts on first test, stops after last; all pass
- `node --experimental-strip-types -e "import('./extensions/caldav.ts')"` -- extension loads via jiti without errors

**Manual checks:**
- Extension loads in pi-agent (`session_start` logs `[caldav] extension loaded`) and registers 3 tools

## Review Triage Log

<!-- Append-only. Populated by step-04 on every review pass: one row per reviewer finding —
     verdict (high/medium/low/false/maybe-false) with its evidence: the refutation for
     false, what would settle it for maybe-false. Empty until the first review pass. -->

- [false] I/O Matrix `Non-2xx → error with body`: the code throws on non-2xx with HTTP status + headers (verified via `--status=500` stub: `WebDAV PUT failed: HTTP 500`). No response body — only the spec's wording is imprecise; no code defect.
- [patch] `resolveCalendar()` in `extensions/caldav.ts`: an empty/blank calendar now throws `calendar is required` (verified: blank calendar → clear error, not silent fallback).
- [patch] `executeCreateEvent()` in `extensions/caldav.ts`: missing `start`/`end` now throws `calendar_create_event requires a start and an end date` before any discovery or PUT (verified with `--mode=unit` create without dates).
- [x] `handover.md` claimed 17 tests while `tests/test_caldav.py` has 19 — fixed in handover.md (spec already lists 19 in its task list).
- [defer] `package.json` `files` lists non-existent `scripts`/`LICENSE`; the package is not published (not in `~/.pi/agent/settings.json`) — pre-existing scaffolding, out of scope.
- [defer] `executeTaskList()` maps a bare name to `/remote.php/dav/calendars/<user>/tasks`, matching the handover decision on task endpoints; only the tool description ("user name or task-list href") needs clarification.
