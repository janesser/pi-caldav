// @ts-nocheck
// TypeScript types are provided by pi at runtime via jiti.
// This extension uses runtime-only imports; type checking is skipped.

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

// ── Configuration (env-injected, never committed) ─────────────────────

const PI_CALENDAR_URL = process.env.PI_CALENDAR_URL || "";
const PI_CALENDAR_USER = process.env.PI_CALENDAR_USER || "";
const PI_CALENDAR_PASSWORD = process.env.PI_CALENDAR_PASSWORD || "";

// Captured from the pi API object handed to the extension factory. Nested
// helpers (webdav, propfind, tools) run in closures over this scope.
// let piApi: ExtensionAPI;

// Task list collections live under `/remote.php/dav/calendars/<user>/tasks`
// (Nextcloud's Tasks app). Everything is enumerated at request time so no
// stale collection state leaks between sessions.
const TASK_LIST_ROOT = "/remote.php/dav/calendars/";

// ── iCal helpers (VEVENT / VTODO extraction) ─────────────────────────

/** Normalize an ISO-8601 value the way CalDAV servers emit it. */
function normalizeIso(raw: string): string {
	return raw ? raw.trim().replace(/Z$/, "") : "";
}

/** Parse one line of a VEVENT/VTODO block into a property. */
export default function (pi: any) { return {} }
