# pi-caldav — Handover

## State (2026-11-13)

Implemented. `extensions/caldav.ts` is a single curl-driven WebDAV client with all three tool
payloads; the request layer, collection discovery, iCal/VTODO parsing, and error handling are
complete and covered by 19 tests (unit + integration).

## What was decided

- **Option B** — a *separate* package (`@janesser/pi-caldav`), not an addition to `pi-signal`.
  Registers tools against pi-agent's `pi.registerTool()` API, same pattern as
  `@aalzubidy/pi-signal` (installed at `~/.pi/agent/npm/node_modules/@aalzubidy/pi-signal`).
- **Two tool families** — events (`calendar_list_events`, `calendar_create_event`) and tasks
  (`task_list`). The package ships both in one go.
- **Credential model** — env vars take precedence; if unset, the extension reads them from
  `~/.pi/agent/.env-caldav` (chmod 600), which is why pi does not need shell-level wiring at all.
  Never committed.
- **Runtime strategy** — on-demand curl-driven requests (no persistent daemon). Reuses the
  existing `pi.exec("curl", ...)` pattern used by `signal_send`.
- Discovery enumerates collections at request time, never from a module-level cache.
- Tasks live under `/remote.php/dav/calendars/<user>/tasks` (Nextcloud's Tasks app).

## Technical facts (verified, not memory)

- A previously configured SOGo calendar (`<old-caldav-hostname>`) is **dead** (302 → 404).
- Successor: **Managed Nextcloud** at `https://<caldav-server-hostname>/`, nginx reverse proxy.
- CalDAV endpoints exist and are auth-gated (they return 401 to a probe):
  - `/remote.php/caldav/calendars/<user>/<calendar>/` — events
  - `/remote.php/dav/calendars/<user>/<calendar>/` — events (alternate path)
  - `/remote.php/dav/calendars/<user>/tasks` — task lists
  - `/remote.php/dav/addressbooks/users/<username>/contacts/` — CardDAV (already wired in Evolution as `contacts-jan`)
- Outbound HTTPS from every box reaches `<caldav-server-hostname>:443` (confirmed via live 401).
- **No CalDAV client is installed** on any box (`caldav`, `vobject` both missing).

## Tasks & Acceptance — done

- [x] `extensions/caldav.ts` — WebDAV client: PROPFIND collection discovery, REPORT event query
  with time-range filter, MKCALENDAR/PUT for events, basic-auth headers, error parsing.
- [x] `extensions/caldav.ts` — tool payloads: VEVENT/VTODO parsing, 401/404/timeout handling.
- [x] `tests/mock_caldav_server.py` — hand-rolled stdlib-only mock on a free port serving the
  Nextcloud-style CalDAV paths with fixture XML for PROPFIND/REPORT/MKCALENDAR/PUT.
- [x] `tests/extension_runner.js` — node harness in `unit`, `int` and `tools` modes.
- [x] `tests/test_caldav.py` — 19 unittest tests (stubbed-exec unit + real-HTTP integration).
- [x] `skills/caldav/SKILL.md`, README, handover — updated with the implemented state.

**Acceptance criteria met:**
- Given a CalDAV-compatible URL, `calendar_list_events` returns real event summaries parsed
  from iCal via the HTTP layer.
- Given fixture XML and a mock server, all 19 tests pass without touching the network.
- Given unauthenticated or unreachable state, any tool fails with a clear error message.

## Test strategy (for the next session)

`python3 -m unittest discover -s tests` starts `tests/mock_caldav_server.py` on a free port in
the first test and stops it after the last one. Every test goes through `run_tool()` which:
sets up credentials, runs `node --experimental-strip-types tests/extension_runner.js`, and
returns the tool output plus the recorded exec calls.

- **Unit mode** (`--mode=unit`): `pi.exec` is stubbed; `--fixture=<name>` supplies the response
  body and `--discovery-fixture=<name>` only the discovery PROPFIND body (unit tests for
  `task_list` need both). `--status=<code>` and `--fail` emulate error responses. Unit mode
  never makes a network call — it verifies request formation and payload parsing.
- **Integration mode** (`--mode=int`, pass `env=self.base_env`): the extension's real curl
  calls hit the mock over HTTP, so the full client stack is verified (including the unit
  fixtures' XML for REPORT responses).

## Open questions for the next session

- Credential wiring is now automatic: the extension reads `~/.pi/agent/.env-caldav`
  (chmod 600) at load time, with env vars still taking precedence when both are present. The
  app password lives there — never committed.
- Real-world verification against `https://<caldav-server-hostname>/` once auth is provisioned.
