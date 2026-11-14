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
function parseICalProperty(line: string): [string, string] | null {
	const m = /^([A-Z]+)(?::[^@]*)?(?:[ \t]*[;,].*)?(.*)$/.exec(line);
	if (!m || !m[1]) return null;
	return [m[1], m[2].trim()];
}

/**
 * Split iCal content into VEVENT / VTODO blocks and extract the properties of
 * every block. Returns one record per VEVENT/VTODO.
 */
interface ICalRecord {
	summary: string;
	start: string;
	end: string;
	uid: string;
	description: string;
	location: string;
	due?: string;
	status?: string;
	raw: string;
}

function parseICalBlocks(ical: string): ICalRecord[] {
	const records: ICalRecord[] = [];
	let currentType = "";
	let buffer = "";
	for (const line of ical.split(/\r?\n/)) {
		const upper = line.trim().toUpperCase();
		if (upper.startsWith("BEGIN:") && /VEVENT|VTODO/.test(upper)) {
			currentType = upper.slice(6).replace(/[^A-Z]/g, "");
			buffer = "";
		} else if ((upper === "END:VEVENT" || upper === "END:VTODO") && currentType) {
			const props: Record<string, string> = {};
			for (const l of buffer.split(/\r?\n/)) {
				const p = parseICalProperty(l);
				if (p) props[p[0]] = p[1];
			}
			records.push({
				summary: props["SUMMARY"] || "",
				start: normalizeIso(props["DTSTART"]),
				end: normalizeIso(props["DTEND"]),
				uid: props["UID"] || "",
				description: props["DESCRIPTION"] || "",
				location: props["LOCATION"] || "",
				due: normalizeIso(props["DUE"]),
				status: props["STATUS"],
				raw: buffer.trim(),
			});
			currentType = "";
			buffer = "";
		} else if (currentType) {
			buffer += line + "\n";
		}
	}
	return records;
}

// ── XML parsing (self-contained, no external dependency) ──────────────

type XmlNode = {
	tag: string;
	attrs: Record<string, string>;
	children: XmlNode[];
	text?: string;
};

/** Strip a namespace prefix ("d:href" → "href"). */
function localName(tag: string): string {
	return tag.split(":").pop() || "";
}

/** Find all descendant elements whose local name matches `needle`. */
function findAll(root: XmlNode | null, needle: string): XmlNode[] {
	const out: XmlNode[] = [];
	if (!root) return out;
	function walk(node: XmlNode) {
		if (localName(node.tag).toLowerCase() === needle.toLowerCase()) out.push(node);
		for (const child of node.children) walk(child);
	}
	walk(root);
	return out;
}

function findText(root: XmlNode | null, needle: string): string {
	const node = findAll(root, needle)[0];
	if (!node) return "";
	return (node.text || "").trim();
}

/** Minimal recursive-descent XML parser. */
function parseXml(body: string): XmlNode | null {
	let i = 0;
	const len = body.length;

	function ws() {
		while (i < len && /[\t\n\r ]/.test(body[i])) i++;
	}

	function parseAttrs(): Record<string, string> {
		const attrs: Record<string, string> = {};
		ws();
		while (i < len && body[i] !== ">" && body[i] !== "/" && body[i] !== "?") {
			let name = "";
			while (i < len && body[i] !== "=" && !/[\t\n\r ]/.test(body[i])) {
				name += body[i];
				i++;
			}
			if (!name) break;
			while (i < len && /[\t\n\r ]/.test(body[i])) i++;
			if (body[i] === "=") i++;
			ws();
			const quote = body[i];
			if (quote !== '"' && quote !== "'") {
				let value = "";
				while (i < len && body[i] !== " " && body[i] !== ">" && body[i] !== "/") {
					value += body[i];
					i++;
				}
				attrs[name.trim().toLowerCase()] = value;
				continue;
			}
			i++;
			let value = "";
			while (i < len && body[i] !== quote) {
				value += body[i];
				i++;
			}
			if (i < len) i++;
			attrs[name.trim().toLowerCase()] = value;
			ws();
		}
		return attrs;
	}

	function parseElement(): XmlNode | null {
		if (body[i] !== "<") return null;
		i++;
		let tag = "";
		while (i < len && body[i] !== ">" && body[i] !== " ") tag += body[i++];
		const attrs = parseAttrs();
		ws();
		if (i < len && (body[i] === ">" || body[i] === "/")) {
			i++; // consume '>' or '/>'
			return { tag, attrs, children: [], text: undefined };
		}
		const children: XmlNode[] = [];
		let text = "";
		while (i < len && !(body[i] === "<" && body[i + 1] === "/")) {
			if (body[i] === "<") {
				if (body.slice(i, i + 9) === "<![CDATA[") {
					// CDATA section: treat it as element text.
					const end = body.indexOf("]]>", i);
					text += body.slice(i + 9, end === -1 ? len : end);
					i = end === -1 ? len : end + 3;
					continue;
				}
				const child = parseElement();
				if (child) children.push(child);
			} else {
				text += body[i];
				i++;
			}
		}
		// consume the closing tag `</tag>`
		if (i < len && body[i] === "<" && body[i + 1] === "/") {
			i += 2;
			while (i < len && body[i] !== ">") i++;
			if (i < len) i++;
		}
		return { tag, attrs, children, text: text.trim() };
	}

	const root = parseElement();
	return root;
}

// ── WebDAV client ─────────────────────────────────────────────────────

interface WebDavResult {
	code: number;
	headers: Record<string, string>;
	body: string;
}

/** Build the curl argument list for one WebDAV round trip. */
function buildCurlArgs(method: string, url: string, body?: string): string[] {
	return [
		"-s",
		"--basic",
		"-u",
		`${PI_CALENDAR_USER}:${PI_CALENDAR_PASSWORD}`,
		"-D",
		"-", // dump headers to stdout (before the body)
		"-w",
		"\n%{http_code}", // status code as the last line of stdout
		"-X",
		method,
		"--max-time",
		"30",
		url,
	];
}

/** Normalize an href (optionally base-prefixed) into a full URL. */
function normalizeHref(href: string): string {
	const path = toPath(href);
	return `${normalizeBase()}/${path}`;
}

async function webdav(
	method: string,
	path: string,
	body?: string,
): Promise<WebDavResult> {
	const result = await piApi.exec("curl", buildCurlArgs(method, normalizeHref(path), body), {
		timeout: 40_000,
	});
	if (result.killed) {

/** Parse the header/body/status stream that `webdav` emits on stdout. */
function parseWebDavResponse(stdout: string): WebDavResult {
	// Trailing newlines come from `-s`/header dumps; strip them so the status
	// code written by `%{http_code}` is always the last line.
	const trimmed = stdout.replace(/\s+$/, "");
	const lines = trimmed.split("\n");
	let code = 0;
	// The last line is the status code written by `%{http_code}`.
	const tail = lines[lines.length - 1] || "";
	if (/^\d+$/.test(tail)) {
		code = parseInt(tail, 10);
		lines.pop();
	}
	const headerAndBody = lines.join("\n");
	// Header dump ends with a blank line (`\r\n\r\n`), everything after is the body.
	const sep = headerAndBody.indexOf("\r\n\r\n");
	let headers: Record<string, string> = {};
	let body = headerAndBody;
	if (sep !== -1) {
		body = headerAndBody.slice(sep + 4);
		headers = {};
		for (const line of headerAndBody.slice(0, sep + 2).split("\n")) {
			const trimmed = line.replace(/\r$/, "");
			if (!trimmed) continue;
			const idx = trimmed.indexOf(":");
			if (idx === -1) continue;
			headers[trimmed.slice(0, idx).trim().toLowerCase()] = trimmed
				.slice(idx + 1)
				.trim();
		}
	}
	return { code, headers, body };
}

async function webdav(
	method: string,
	path: string,
	body?: string,
): Promise<WebDavResult> {
	const result = await piApi.exec("curl", buildCurlArgs(method, path, body), {
		timeout: 40_000,
	});
	if (result.killed) {
		throw new Error(
			`WebDAV ${method} timed out (${path}): ${result.stderr || "request exceeded 30s"}`,
		);
	}
	if (result.code !== 0) {
		throw new Error(
			`WebDAV ${method} failed (${path}): ${result.stderr || result.stdout || "curl exit code " + result.code}`,
		);
	}
	const res = parseWebDavResponse(result.stdout);
	if (res.code === 401) {
		throw new Error(`Calendar collection not accessible: 401 Unauthorized (${path})`);
	}
	if (res.code === 404) {
		throw new Error(`Calendar collection not found: 404 Not Found (${path})`);
	}
	if (res.code < 200 || res.code >= 300) {
		throw new Error(
			`WebDAV ${method} failed: HTTP ${res.code} (${path}): ${JSON.stringify(res.headers)}`,
		);
	}
	return res;
}

// ── Collection discovery (at request time, never cached) ──────────────

interface CalCollection {
	href: string;
	displayname: string;
	resourcetype: string;
}

/** List the collections a PROPFIND response enumerates. */
function parsePropfind(res: WebDavResult): CalCollection[] {
	const root = parseXml(res.body);
	const collections: CalCollection[] = [];
	for (const response of findAll(root, "response")) {
		const href = findText(response, "href").replace(/\/$/, "");
		if (!href) continue;
		const resourcetypeNode = findAll(response, "resourcetype")[0];
		collections.push({
			href,
			displayname: findText(response, "displayname"),
			resourcetype: resourcetypeNode
				? resourcetypeNode.children.map((c) => localName(c.tag)).join(" ")
				: "",
		});
	}
	return collections;
}

/** Strip a trailing slash so URLs can be concatenated safely. */
function normalizeBase(): string {
	return PI_CALENDAR_URL.replace(/\/$/, "");
}

/** Convert an href (possibly prefixed by the base URL) into a path for curl. */
function toPath(href: string): string {
	const base = normalizeBase();
	if (!base) return href.replace(/^\//, "").replace(/\/+$/, "");
	return href.startsWith(base)
		? href.slice(base.length).replace(/^\//, "").replace(/\/+$/, "")
		: href;
}

/** PROPFIND a path and list its children. */
async function propfind(path: string): Promise<CalCollection[]> {
	const res = await webdav(
		"PROPFIND",
		path,
		`<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:displayname/><d:resourcetype/></d:prop></d:propfind>`,
	);
	return parsePropfind(res);
}

/**
 * Resolve `calendar` to a full calendar href. A bare name is resolved by
 * enumerating all collections at request time (fresh, never cached).
 */
async function resolveCalendar(href: string): Promise<string> {
	if (/^https?:\/\//.test(href) || href.startsWith("/remote.php")) return href;
	const base = normalizeBase();
	const discovery = base
		? `/remote.php/caldav/calendars/${PI_CALENDAR_USER}/`
		: "/remote.php/caldav/calendars/";
	const collections = await propfind(discovery);
	const match = collections.find(
		(c) =>
			c.displayname === href ||
			c.href.endsWith(`/${href}`) ||
			localName(c.href).replace(/\//g, "") === href.replace(/\//g, ""),
	);
	if (!match) {
		throw new Error(
			`Calendar "${href}" not found on the server (no matching collection in the discovered list)`,
		);
	}
	return `${base}${match.href}/`;
}

/** Build a CalDAV REPORT query XML for a time range. */
function reportQueryXml(from: string, to: string): string {
	const attrs = [from && `from="${from}"`, to && `to="${to}"`]
		.filter(Boolean)
		.join(" ");
	const filter = attrs ? `<c:time-range ${attrs}/>` : `<c:time-range/>`;
	return `<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:d="DAV:">
  <d:prop>
    <c:calendar-data>
      <d:prop>
        <d:summary/><d:dtstart/><d:dtend/><d:uid/>
        <d:description/><d:location/>
      </d:prop>
    </c:calendar-data>
  </d:prop>
</c:calendar-query>`;
}

/** Run a CalDAV REPORT and return the raw iCal responses. */
async function reportEvents(calendar: string, from?: string, to?: string): Promise<WebDavResult> {
	return webdav("REPORT", normalizeHref(calendar), reportQueryXml(from, to));
}

/** Parse a REPORT response into events/tasks. */
function parseReport(res: WebDavResult): ICalRecord[] {
	const root = parseXml(res.body);
	const blocks: string[] = [];
	for (const response of findAll(root, "response")) {
		const data = findText(response, "calendar-data");
		if (data) blocks.push(data);
	}
	return parseICalBlocks(blocks.join("\n"));
}

// ── Tools ─────────────────────────────────────────────────────────────

/** List the calendar's events. */
async function executeListEvents(params: { calendar: string; from?: string; to?: string }) {
	const calendar = await resolveCalendar(params.calendar);
	const res = await reportEvents(calendar, params.from, params.to);
	const events = parseReport(res);
	return {
		content: [
			{
				type: "text",
				text: events.length
					? `Events for ${params.calendar} (${events.length} found)\n${events
							.map((e) => `${e.start ? e.start : ""} | ${e.summary || "(no summary)"} | end: ${e.end}`)
							.join("\n")}`
					: `No events found for ${params.calendar}`,
			},
		],
		details: {
			events,
			href: calendar,
			raw_ical: events.map((e) => e.raw).join("\n\n"),
			count: events.length,
		},
	};
}

/** Create an event on a calendar (PUT). */
async function executeCreateEvent(params: {
	calendar: string;
	summary: string;
	start: string;
	end: string;
	description?: string;
	location?: string;
}) {
	const path = toPath(params.calendar);
	if (!path) throw new Error(`calendar parameter is required for calendar_create_event`);

	// Build the VEVENT block.
	let ical = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//pi-caldav//EN\n";
	ical += "BEGIN:VEVENT\n";
	ical += `DTSTART:${params.start.replace(/Z$/, "")}Z\n`;
	ical += `DTEND:${params.end.replace(/Z$/, "")}Z\n`;
	ical += `UID:${Math.random().toString(36).slice(2)}-pi-caldav\n`;
	ical += `SUMMARY:${params.summary}\n`;
	if (params.description) ical += `DESCRIPTION:${params.description}\n`;
	if (params.location) ical += `LOCATION:${params.location}\n`;
	ical += "END:VEVENT\n";
	ical += "END:VCALENDAR\n";

	const res = await webdav("PUT", path, ical);
	return {
		content: [
			{
				type: "text",
				text: res.code === 201 || res.code === 204
					? `Event created on ${params.calendar} (HTTP ${res.code})`
					: `Event created on ${params.calendar} (${JSON.stringify(res.headers)})`,
			},
		],
		details: { calendar: params.calendar, href: normalizeHref(path), status: res.code },
	};
}

/** List tasks from a task list collection (Nextcloud: /remote.php/dav/calendars/<user>/tasks). */
async function executeTaskList(params: { calendar: string }) {
	let path = params.calendar;
	if (!/^https?:\/\//.test(path) && !path.startsWith("/remote.php")) {
		// Resolve the task list to its Nextcloud location before requesting it.
		path = `${TASK_LIST_ROOT}${params.calendar}/tasks`;
	} else {
		path = normalizeHref(path);
	}
	const collections = await propfind(path);
	let target = "";
	for (const c of collections) {
		if (/tasks/i.test(c.href)) {
			target = `${c.href}/`;
			break;
		}
	}
	if (!target && collections.length) target = `${collections[0].href}/`;
	const res = await webdav("REPORT", target, reportQueryXml("", ""));
	const tasks = parseReport(res);
	return {
		content: [
			{
				type: "text",
				text: tasks.length
					? `Tasks for ${params.calendar} (${tasks.length} found)\n${tasks.map((t) => `${t.summary || "(no summary)"} | due: ${t.due || "-"} | status: ${t.status || "-"}`).join("\n")}`
					: `No tasks found for ${params.calendar}`,
			},
		],
		details: {
			tasks,
			href: `${PI_CALENDAR_URL}${target}`,
			raw_ical: tasks.map((t) => t.raw).join("\n\n"),
			count: tasks.length,
		},
	};
}

// ── Extension ──────────────────────────────────────────────────────────

export default function (pi: ExtensionAPI) {
	piApi = pi;

	// ── Tool: calendar_list_events ────────────────────────────────────

	piApi.registerTool({
		name: "calendar_list_events",
		label: "Calendar Events",
		description:
			"List events from a CalDAV calendar. Pass `calendar` (name or URL), plus optional `from`/`to` ISO-8601 dates to bound the query. Returns event summary, start and end times.",
		parameters: Type.Object({
			calendar: Type.String({
				description:
					"The calendar name or full href, e.g. JanEsser or /remote.php/caldav/calendars/JanEsser/Familie-Esser/",
			}),
			from: Type.Optional(
				Type.String({ description: "Start bound (ISO 8601). Defaults to today." }),
			),
			to: Type.Optional(
				Type.String({ description: "End bound (ISO 8601)." }),
			),
		}),
		async execute(
			_toolCallId: string,
			params: { calendar: string; from?: string; to?: string },
			_signal?: AbortSignal,
		) {
			return await executeListEvents(params);
		},
	});

	// ── Tool: calendar_create_event ───────────────────────────────────

	piApi.registerTool({
		name: "calendar_create_event",
		label: "Create Calendar Event",
		description:
			"Create an event on a CalDAV calendar. Pass `calendar` (name or URL), plus `summary`, `start` and `end` ISO-8601 dates, and optional `description`/`location`.",
		parameters: Type.Object({
			calendar: Type.String({ description: "The calendar name or full href." }),
			summary: Type.String({ description: "Event title." }),
			start: Type.String({ description: "Start time (ISO 8601)." }),
			end: Type.String({ description: "End time (ISO 8601)." }),
			description: Type.Optional(Type.String({ description: "Event body." })),
			location: Type.Optional(Type.String({ description: "Event location." })),
		}),
		async execute(
			_toolCallId: string,
			params: { calendar: string; summary: string; start: string; end: string },
			_signal?: AbortSignal,
		) {
			return await executeCreateEvent(params);
		},
	});

	// ── Tool: task_list ───────────────────────────────────────────────

	piApi.registerTool({
		name: "task_list",
		label: "Calendar Tasks",
		description:
			"List VTODO tasks from a CalDAV task list. Pass `calendar` (name or URL) for the calendar that holds the task lists.",
		parameters: Type.Object({
			calendar: Type.String({ description: "The calendar name or full href." }),
		}),
		async execute(
			_toolCallId: string,
			params: { calendar: string },
			_signal?: AbortSignal,
		) {
			return await executeTaskList(params);
		},
	});

	// ── Lifecycle ───────────────────────────────────────────────────────

	piApi.on("session_start", async (_event: unknown, _ctx: unknown) => {
		console.log("[caldav] extension loaded");
		if (!PI_CALENDAR_URL || !PI_CALENDAR_USER || !PI_CALENDAR_PASSWORD) {
			console.warn("[caldav] credentials not set — tools will fail at request time");
		}
	});

	piApi.on("session_shutdown", async () => {
		console.log("[caldav] extension unloaded");
	});
}
