# @janesser/pi-caldav

![pi-caldav](./assets/logo/logo.png)

Connect [pi](https://pi.dev) to a CalDAV server — currently **Managed Nextcloud** at
`https://<caldav-server-hostname>/` — so the agent can read and write calendar events and tasks.

## Tools (LLM-accessible)

| Tool | Purpose |
|---|---|
| `calendar_list_events` | List events, with optional `from`/`to` date bounds |
| `calendar_create_event` | Create an event (`summary`, `start`, `end`, optional `description`/`location`) |
| `task_list` | List VTODO tasks from a task list |

Every tool takes the target calendar as its first parameter — a display name or the full
CalDAV href. A bare name is enumerated at request time, so the list of collections can never
go stale.

## Installation

1. Install the package into pi (from the git repo):

   ```bash
   pi install git:github.com/janesser/pi-caldav
   ```

   Verify it appears in `~/.pi/agent/settings.json` under `"packages"`. The extension is a single file loaded via jiti, so there is no build step.

2. Write the three credentials to `~/.pi/agent/.env-caldav`. The extension picks them up itself, so this file is all it needs — no shell-level wiring required:

   ```bash
   cat > ~/.pi/agent/.env-caldav <<'EOF'
   PI_CALENDAR_URL=https://<caldav-server-hostname>/
   PI_CALENDAR_USER=<username>
   PI_CALENDAR_PASSWORD=<app password>
   EOF
   chmod 600 ~/.pi/agent/.env-caldav
   ```

3. Start pi-agent (or `/reload` an already running agent) and verify the extension starts without a "credentials not set" warning:

   ```bash
   set -a; source ~/.pi/agent/.env-caldav; set +a
   pi <command>
   ```

   Env vars take precedence over the file when both are present, so exporting them is still possible if you prefer, e.g. `set -a; source ~/.pi/agent/.env-caldav; set +a`. If pi-agent is launched by a desktop entry, `tmux`, or a systemd unit instead of your shell, the `.env-caldav` file covers it without any extra wiring.

   The extension is verified offline by `python3 -m unittest discover -s tests`; once the app password is provisioned, test against a live server with a manual probe:

   ```bash
   curl -sI --basic -u <username>:<app-password> https://<caldav-server-hostname>/remote.php/caldav/calendars/<username>/
   ```

## Environment (provisioned, never committed)

| Variable | Purpose |
|---|---|
| `PI_CALENDAR_URL` | CalDAV server base URL (e.g. `https://<caldav-server-hostname>/`) |
| `PI_CALENDAR_USER` | Nextcloud username |
| `PI_CALENDAR_PASSWORD` | **app password** — create it in Nextcloud *Settings → Security* |

## Status

Implemented and tested. The extension is a single curl-driven WebDAV client loaded via jiti
(`extensions/caldav.ts`) with no build step:

- `calendar_list_events` — PROPFIND discovery, REPORT time-range query, iCal VEVENT parsing
  (summary, start, end, uid, description, location).
- `calendar_create_event` — resolves the calendar and PUTs a VEVENT to the collection.
- `task_list` — enumerates `/remote.php/dav/calendars/<user>/tasks` and parses VTODOs
  (summary, due, status, uid).

## Testing

No network is touched: `python3 -m unittest discover -s tests`.

| Mode | How it works |
|---|---|
| `unit` | `tests/extension_runner.js --mode=unit` stubs the exec layer and feeds fixture XML,
  so request formation and iCal/XML parsing are verified without HTTP. |
| `int` | The extension's real curl calls reach `tests/mock_caldav_server.py`, a hand-rolled
  stdlib-only Nextcloud-style mock on a free port. |

```bash
python3 -m unittest discover -s tests   # all unit + integration tests, no network
node --experimental-strip-types -e "import('./extensions/caldav.ts')"  # extension loads
```

## Conventions (from pi-signal)

- `extensions/*.ts` is loaded via jiti by pi — runtime-only imports, no build step.
- `Type.Object(...)` from `typebox` for tool parameters (pi-agent exposes them to the LLM).
- Return `{ content: [{ type: "text", text }], details: {} }` from `execute()`.
- Keep secrets age-encrypted; env vars are the delivery mechanism.
