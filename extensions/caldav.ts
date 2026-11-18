// TypeScript types are provided by pi at runtime via jiti.
// This extension uses runtime-only imports; type checking is skipped.

import type {
	ExtensionAPI,
	ExtensionContext,
	Theme,
	ThemeColor,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { readFileSync } from "node:fs";

// ── Configuration (env-injected, never committed) ─────────────────────

/** Well-known locations of a KEY=VALUE config file, checked in order. */
const CONFIG_FILES = [
	"$PI_CODING_AGENT_DIR/.env-caldav",
	"$HOME/.pi/agent/.env-caldav",
	"~/.pi/agent/.env-caldav",
];

/** Read a KEY=VALUE file, ignoring blank lines and comments. Null when unreadable. */
function readEnvFile(path: string): Record<string, string> | null {
	try {
		const content = readFileSync(path, "utf8");
		const config: Record<string, string> = {};
		for (const line of content.split(/\r?\n/)) {
			const trimmed = line.trim();
			if (!trimmed || trimmed.startsWith("#")) continue;
			const idx = trimmed.indexOf("=");
			if (idx === -1) continue;
			config[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
		}
		return config;
	} catch (_err) {
		return null;
	}
}

/** Expand $VARS and ~ in a path spec. */
function resolvePathSpec(spec: string): string {
	const expanded = spec.replace(/\$([A-Z_0-9]+)\b/g, (_, name) => process.env[name] || "");
	if (!expanded.startsWith("~")) return expanded;
	return `${process.env.HOME}/${expanded.slice(2)}`;
}

/** Read one credential from the config file, if present. */
function readCredentialFromFile(key: string): string {
	for (const spec of CONFIG_FILES) {
		const config = readEnvFile(resolvePathSpec(spec));
		if (!config || !config[key]) continue;
		return config[key];
	}
	return "";
}

/** Resolve one credential key from an env var, falling back to a config file. */
function resolveConfigValue(key: string): string {
	return process.env[key] || readCredentialFromFile(key);
}

const PI_CALENDAR_URL = resolveConfigValue("PI_CALENDAR_URL");
const PI_CALENDAR_USER = resolveConfigValue("PI_CALENDAR_USER");
const PI_CALENDAR_PASSWORD = resolveConfigValue("PI_CALENDAR_PASSWORD");

// Captured from the pi API object handed to the extension factory. Nested
// helpers (webdav, propfind, tools) run in closures over this scope.
let piApi: ExtensionAPI;

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
	// The name is everything up to the first `;` (params) or `:` (value). A
	// property always carries its value after the colon, so capture it directly
	// instead of relying on an optional group that greedily swallows it.
	const m = /^([A-Z]+)(?:;[^:]*)?(?::(.+))?/.exec(line);
	if (!m || !m[1]) return null;
	return [m[1], (m[2] || "").trim()];
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
	href?: string;
	component?: string;   // VEVENT / VTODO
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
				// `.raw` is the block's content without its BEGIN:/END: wrapper, so the
				// component name lets callers rebuild a complete record.
				component: currentType,
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

	// Skip an XML declaration (`<?xml ...?>`) if present so the document root is
	// parsed rather than the declaration itself.
	if (body.startsWith("<?xml")) {
		const end = body.indexOf("?>");
		if (end !== -1) i = end + 2;
	}
	// Skip any whitespace after the declaration so `parseElement` always sees the
	// root element and not a leftover newline.
	ws();

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
		if (i < len && body[i] === "/") {
			// Self-closing element (`<tag .../>`).
			i++; // consume '/'
			if (i < len && body[i] === ">") i++;
			return { tag, attrs, children: [], text: undefined };
		}
		if (i < len && body[i] === ">") {
			i++; // consume '>'
			ws();
			// Empty element (`<tag></tag>`) when the closing tag follows immediately.
			const close = `</${tag}>`;
			if (body.slice(i, i + close.length) === close) {
				i += close.length;
				return { tag, attrs, children: [], text: undefined };
			}
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
function buildCurlArgs(method: string, url: string, body?: string, headers?: string[]): string[] {
	const args = [
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
	for (const header of headers ?? []) args.push("-H", header);
	if (body) args.push("--data-raw", body);
	return args;
}

/** Normalize an href (optionally base-prefixed) into a full URL. */
function normalizeHref(href: string): string {
	return toUrl(href);
}

/** Turn a bare path into a full URL using the configured base, leaving URLs intact. */
function toUrl(path: string): string {
	if (/^https?:\/\//.test(path)) return path;
	const base = normalizeBase();
	return base ? `${base}/${path.replace(/^\//, "")}` : path;
}

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
	headers?: string[],
): Promise<WebDavResult> {
	const url = toUrl(path);
	const result = await piApi.exec("curl", buildCurlArgs(method, url, body, headers), {
		timeout: 40_000,
	});
	if (result.killed) {
		throw new Error(
			`WebDAV ${method} timed out (${url}): ${result.stderr || "request exceeded 30s"}`,
		);
	}
	if (result.code !== 0) {
		throw new Error(
			`WebDAV ${method} failed (${url}): ${result.stderr || result.stdout || "curl exit code " + result.code}`,
		);
	}
	const res = parseWebDavResponse(result.stdout);
	if (res.code === 401) {
		throw new Error(`Calendar collection not accessible: 401 Unauthorized (${url})`);
	}
	if (res.code === 404) {
		throw new Error(`Calendar collection not found: 404 Not Found (${url})`);
	}
	if (res.code < 200 || res.code >= 300) {
		throw new Error(
			`WebDAV ${method} failed: HTTP ${res.code} (${url}): ${JSON.stringify(res.headers)}`,
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
		: href.replace(/^\//, "").replace(/\/+$/, "");
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

/** Discovery roots exposed by Nextcloud; the first that responds wins. */
const CALENDAR_ROOTS = ["/remote.php/caldav/calendars/", "/remote.php/dav/calendars/"];

/** Whether a discovered collection matches one calendar name or href segment. */
function matchesCollection(c: CalCollection, href: string): boolean {
	return (
		c.displayname === href ||
		c.href.endsWith(`/${href}`) ||
		localName(c.href).replace(/\//g, "") === href.replace(/\//g, "")
	);
}

/**
 * Resolve `calendar` to a full calendar href. A bare name is resolved by
 * enumerating all collections at request time (fresh, never cached).
 */
async function resolveCalendar(href: string): Promise<string> {
	if (/^https?:\/\//.test(href) || href.startsWith("/remote.php")) return href;
	const base = normalizeBase();
	// 401/404 from the first discovery root is surfaced (it points at the real server);
	// only unreachable roots are retried against the other one.
	let lastError: Error | undefined;
	for (const root of CALENDAR_ROOTS) {
		let collections: CalCollection[] = [];
		try {
			collections = await propfind(`${root}${PI_CALENDAR_USER}/`);
		} catch (err) {
			lastError ??= err instanceof Error ? err : new Error(String(err));
			continue;
		}
		if (!collections.length) continue;
		const match = collections.find((c) => matchesCollection(c, href));
		if (match) return `${base}${match.href}/`;
	}
	if (lastError) throw lastError;
	if (!href.trim()) throw new Error("calendar is required");
	throw new Error(
		`Calendar "${href}" not found on the server (no matching collection in the discovered list)`,
	);
}

/** Build a CalDAV REPORT query XML for one component type.

 * SabreDAV (Nextcloud's CalDAV backend) requires a `<c:filter>` element with a
 * direct `<c:comp-filter name="..."/>` child, and the time-range must live inside
 * that comp-filter — a time-range on the VCALENDAR level is rejected. An empty
 * comp-filter returns all objects of that component type. */
function reportQueryXml(component: string, from?: string, to?: string): string {
	const attrs = [from && `from="${from}"`, to && `to="${to}"`]
		.filter(Boolean)
		.join(" ");
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
  <c:filter>
    <c:comp-filter name="${component}">${attrs ? `<c:time-range ${attrs}/>` : ""}</c:comp-filter>
  </c:filter>
</c:calendar-query>`;
}

/** Run a CalDAV calendar-query REPORT and return the raw iCal responses. */
async function reportEvents(
	calendar: string,
	from?: string,
	to?: string,
	component = "VEVENT",
): Promise<WebDavResult> {
	return webdav("REPORT", normalizeHref(calendar), reportQueryXml(component, from, to), ["Depth: 1"]);
}

/** Parse a REPORT/PROPFIND response into events/tasks, carrying each resource href. */
function parseReport(res: WebDavResult): ICalRecord[] {
	const root = parseXml(res.body);
	const records: ICalRecord[] = [];
	for (const response of findAll(root, "response")) {
		const data = findText(response, "calendar-data");
		if (!data) continue;
		// The resource href is reported alongside its iCal so writes can target it.
		const href = findText(response, "href").replace(/\/$/, "");
		for (const record of parseICalBlocks(data)) {
			records.push({ ...record, href });
		}
	}
	return records;
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
							.map((e) => `${e.start ? e.start : ""} | ${e.summary || "(no summary)"} | end: ${e.end} | href: ${e.href}`)
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
	// Validate the parameters before any discovery or PUT work.
	if (!params.start || !params.end) {
		throw new Error(`calendar_create_event requires a start and an end date`);
	}
	// A bare name needs discovery to learn the calendar's href before PUTting.
	const calendar = await resolveCalendar(params.calendar);
	const path = toPath(calendar);
	if (!path) throw new Error(`calendar parameter is required for calendar_create_event`);

	// Build the VEVENT block (Nextcloud requires the PUT resource to carry a name).
	const uid = `${Math.random().toString(36).slice(2)}-pi-caldav`;
	let ical = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//pi-caldav//EN\n";
	ical += "BEGIN:VEVENT\n";
	ical += `DTSTART:${params.start.replace(/Z$/, "")}Z\n`;
	ical += `DTEND:${params.end.replace(/Z$/, "")}Z\n`;
	ical += `UID:${uid}\n`;
	ical += `SUMMARY:${params.summary}\n`;
	if (params.description) ical += `DESCRIPTION:${params.description}\n`;
	if (params.location) ical += `LOCATION:${params.location}\n`;
	ical += "END:VEVENT\n";
	ical += "END:VCALENDAR\n";

	const res = await webdav("PUT", `${path}/${uid}.ics`, ical);
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

/**
 * Resolve the task list collection that a write targets.
 *
 * A bare name resolves to the user's task list collection (the well-known
 * Nextcloud location), an href is used as-is. Always at request time so no
 * stale collection state leaks between sessions.
 */
async function resolveTaskCollection(calendar: string): Promise<string> {
	// An explicit href is used as-is; discovery is only needed for a bare name.
	if (/^https?:\/\//.test(calendar) || calendar.startsWith("/remote.php")) return normalizeHref(calendar);
	return `${TASK_LIST_ROOT}${PI_CALENDAR_USER}/tasks`;
}

/** Build a task resource URL from an href, or from list + UID (`task_list` reports it back). */
async function resolveTaskResource(params: {
	href?: string;
	uid?: string;
	calendar?: string;
}): Promise<string | undefined> {
	if (params.href) return normalizeHref(params.href);
	if (!params.uid || !params.calendar) return undefined;
	const collection = await resolveTaskCollection(params.calendar);
	return `${collection.replace(/\/$/, "")}/${params.uid}.ics`;
}

/** Read one CalDAV resource's iCal body (PROPFIND with calendar-data).
 *
 * A PUT replaces the whole resource, so writes need to patch the existing body
 * instead of clobbering it — this is how the current content is fetched.
 */
async function readResource(resource: string): Promise<string> {
	const res = await webdav(
		"PROPFIND",
		normalizeHref(resource),
		`<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-data/></d:prop></d:propfind>`,
		["Depth: 1"],
	);
	const records = parseReport(res);
	if (!records.length) return "";
	// `.raw` is the block's content without its BEGIN:/END: wrapper, so rebuild a
	// complete record — a fragment would not re-parse.
	const { component, raw } = records[0];
	return `BEGIN:${component}\n${raw}\nEND:${component}\n`;
}

/**
 * Resolve an event resource URL from an href, or from the event's calendar and UID.
 *
 * An event lives under `<calendar>/<uid>.ics` in the calendar collection that holds
 * it (which `resolveCalendar` enumerates at request time), unlike a task that lives
 * in the user's task list.
 */
async function resolveEventResource(params: {
	href?: string;
	uid?: string;
	calendar?: string;
}): Promise<string | undefined> {
	if (params.href) return normalizeHref(params.href);
	if (!params.uid || !params.calendar) return undefined;
	const collection = await resolveCalendar(params.calendar);
	return `${collection.replace(/\/$/, "")}/${params.uid}.ics`;
}

/**
 * Normalize an iCal date the way this extension emits it: UTC datetimes keep their Z,
 * plain dates stay bare (so a VEVENT's DTSTART/DTEND are rebuilt correctly). The value
 * the parser hands back has already lost its Z suffix.
 */
function formatIsoDate(raw?: string): string {
	return raw ? (/T/.test(raw) ? `${raw.replace(/Z$/, "")}Z` : raw) : "";
}

/** Update a VEVENT (PUT). Nextcloud stores each event as its own resource. */
async function executeEventUpdate(params: {
	href?: string;
	uid?: string;
	calendar?: string;
	summary?: string;
	start?: string;
	end?: string;
	description?: string;
	location?: string;
	status?: string;
}) {
	const href = await resolveEventResource(params);
	if (!href) throw new Error(`event_update requires an href, or a calendar and uid`);

	// The resource is replaced on PUT, so patch its current body instead of clobbering it.
	const ical = await readResource(href);
	if (!ical) throw new Error(`Event not found: ${href}`);

	const record = parseICalBlocks(ical)[0];
	let body = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//pi-caldav//EN\n";
	body += "BEGIN:VEVENT\n";
	body += `UID:${record.uid}\n`;
	body += `SUMMARY:${params.summary ?? record.summary}\n`;
	body += `DTSTART:${formatIsoDate(params.start || record.start)}\n`;
	body += `DTEND:${formatIsoDate(params.end || record.end)}\n`;
	if (params.description) body += `DESCRIPTION:${params.description}\n`; else if (record.description) body += `DESCRIPTION:${record.description}\n`;
	if (params.location) body += `LOCATION:${params.location}\n`; else if (record.location) body += `LOCATION:${record.location}\n`;
	if (params.status) body += `STATUS:${params.status}\n`;
	body += "END:VEVENT\n";
	body += "END:VCALENDAR\n";

	const res = await webdav("PUT", toPath(href), body);
	return {
		content: [
			{
				type: "text",
				text: res.code === 201 || res.code === 204
					? `Event ${record.uid} updated (HTTP ${res.code})`
					: `Event ${record.uid} updated (${JSON.stringify(res.headers)})`,
			},
		],
		details: { href, status: res.code },
	};
}

/** Delete an event resource (DELETE). Nextcloud stores each event as its own resource. */
async function executeEventDelete(params: {
	href?: string;
	uid?: string;
	calendar?: string;
}) {
	const href = await resolveEventResource(params);
	if (!href) throw new Error(`event_delete requires an href, or a calendar and uid`);

	const res = await webdav("DELETE", toPath(href));
	return {
		content: [
			{
				type: "text",
				text: res.code === 204 || res.code === 200
					? `Event ${href} deleted`
					: `Event ${href} deleted (${JSON.stringify(res.headers)})`,
			},
		],
		details: { href, status: res.code },
	};
}

/** Update a VTODO's status (PUT). Nextcloud stores each task as its own resource. */
async function executeTaskUpdateStatus(params: {
	href?: string;
	uid?: string;
	calendar?: string;
	status: string;
}) {
	// Validate the parameters before any discovery or PUT work.
	if (!params.status) throw new Error(`task_update_status requires a status`);
	const href = await resolveTaskResource(params);
	if (!href) throw new Error(`task_update_status requires an href, or a calendar and uid`);

	// The resource is replaced on PUT, so patch its current body instead of clobbering it.
	const ical = await readResource(href);
	if (!ical) throw new Error(`Task not found: ${href}`);

	const record = parseICalBlocks(ical)[0];
	let body = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//pi-caldav//EN\n";
	body += "BEGIN:VTODO\n";
	body += `UID:${record.uid}\n`;
	body += `SUMMARY:${record.summary}\n`;
	// iCal date-times are emitted in UTC, so keep the value there (the parsed value
	// has lost its Z suffix); plain dates stay as-is.
	const due = record.due ? (/T/.test(record.due) ? `${record.due}Z` : record.due) : "";
	if (due) body += `DUE:${due}\n`;
	if (record.description) body += `DESCRIPTION:${record.description}\n`;
	body += `STATUS:${params.status}\n`;
	body += "END:VTODO\n";
	body += "END:VCALENDAR\n";

	const res = await webdav("PUT", toPath(href), body);
	return {
		content: [
			{
				type: "text",
				text: res.code === 201 || res.code === 204
					? `Task ${record.uid} status updated to ${params.status} (HTTP ${res.code})`
					: `Task ${record.uid} status updated to ${params.status} (${JSON.stringify(res.headers)})`,
			},
		],
		details: { href, status: params.status, uid: record.uid, calendar: params.calendar },
	};
}

/** Delete a task resource (DELETE). Nextcloud stores each task as its own resource. */
async function executeTaskDelete(params: {
	href?: string;
	uid?: string;
	calendar?: string;
}) {
	const href = await resolveTaskResource(params);
	if (!href) throw new Error(`task_delete requires an href, or a calendar and uid`);

	const res = await webdav("DELETE", toPath(href));
	return {
		content: [
			{
				type: "text",
				text: res.code === 204 || res.code === 200
					? `Task ${href} deleted`
					: `Task ${href} deleted (${JSON.stringify(res.headers)})`,
			},
		],
		details: { href, status: res.code },
	};
}

/** Create a VTODO in a task list collection (PUT). */
async function executeTaskCreate(params: { calendar: string; summary: string; due?: string; description?: string }) {
	// Validate the parameters before any discovery or PUT work.
	if (!params.summary) {
		throw new Error(`task_create requires a summary`);
	}
	const collection = await resolveTaskCollection(params.calendar);
	const path = toPath(collection);
	if (!path) throw new Error(`calendar parameter is required for task_create`);

	// Build the VTODO block (Nextcloud stores each task as its own resource).
	const uid = `${Math.random().toString(36).slice(2)}-pi-caldav`;
	let ical = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//pi-caldav//EN\n";
	ical += "BEGIN:VTODO\n";
	ical += `UID:${uid}\n`;
	ical += `SUMMARY:${params.summary}\n`;
	if (params.due) ical += `DUE:${params.due.replace(/Z$/, "")}Z\n`;
	if (params.description) ical += `DESCRIPTION:${params.description}\n`;
	ical += "END:VTODO\n";
	ical += "END:VCALENDAR\n";

	const res = await webdav("PUT", `${path}/${uid}.ics`, ical);
	return {
		content: [
			{
				type: "text",
				text: res.code === 201 || res.code === 204
					? `Task created on ${params.calendar} (HTTP ${res.code})`
					: `Task created on ${params.calendar} (${JSON.stringify(res.headers)})`,
			},
		],
		details: { calendar: params.calendar, href: `${path}/${uid}.ics`, status: res.code },
	};
}

/** List tasks from a task list collection (Nextcloud: /remote.php/dav/calendars/<user>/tasks). */
async function executeTaskList(params: { calendar: string }) {
	let path = params.calendar;
	if (!/^https?:\/\//.test(path) && !path.startsWith("/remote.php")) {
		// A bare name resolves to the user's task list collection.
		path = `${TASK_LIST_ROOT}${params.calendar.replace(/\/+$/, "")}/tasks`;
	} else {
		path = normalizeHref(path);
	}
	const collections = await propfind(path);
	let target = "";
	for (const c of collections) {
		if (/tasks/i.test(c.href)) {
			target = `${c.href.replace(/\/+$/, "")}/`;
			break;
		}
	}
	if (!target && collections.length) target = `${collections[0].href.replace(/\/+$/, "")}/`;
	const res = await reportEvents(target, undefined, undefined, "VTODO");
	const tasks = parseReport(res);
	return {
		content: [
			{
				type: "text",
				text: tasks.length
					? `Tasks for ${params.calendar} (${tasks.length} found)\n${tasks.map((t) => `${t.summary || "(no summary)"} | due: ${t.due || "-"} | status: ${t.status || "-"} | href: ${t.href}`).join("\n")}`
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

// ── Status bar widget — show which CalDAV remotes are connected ────────
/**
 * The extension reports its connection in the chat window's status bar,
 * modelled on Crossbar's loaded-model widget: a marker, the server host and what
 * is reachable there. It is best-effort: never throws, never blocks startup.
 *
 * Pure functions:
 *  - `formatCaldavState`
 *  - `probeCaldav`
 *
 * Installation (in the lifecycle block): wires to `ctx.ui.setStatus` on
 * `session_start` and clears it on `session_shutdown`.
 */

/** Status key used with `ctx.ui.setStatus`, rendered in the chat window. */
const STATUS_KEY = "caldav";

/** How the extension reports its connection to the chat window's status bar. */
type CalDavState =
	| { kind: "unconfigured" }                 // no credentials found anywhere
	| { kind: "auth-failed"; url: string }     // 401 from the server
	| { kind: "unreachable"; url: string; error: string }  // no response at all
	| { kind: "connected"; url: string; collections: CalCollection[] };  // connected + discovered

/** Fallback for environments without a TUI theme (no colors, plain text). */
function defaultTheme(): Pick<Theme, "fg"> {
	return { fg: (_color: ThemeColor, text: string) => text };
}

/** Render one connection state as a status-bar line, or "" to clear it. */
export function formatCaldavState(state: CalDavState, theme?: Pick<Theme, "fg">): string {
	const fg = (color: ThemeColor, text: string) => (theme?.fg ?? defaultTheme().fg)(color, text);

	if (state.kind === "unconfigured") {
		return `${fg("warning", "\u2716")} no credentials`;
	}

	// Only the host matters here — paths are for the tools. `.host` keeps the port
	// when it is not 80/443, so self-hosted servers stay identifiable.
	let host: string;
	try {
		host = new URL(state.url).host.replace(/^www\./, "");
	} catch {
		host = state.url;
	}

	if (state.kind === "auth-failed") {
		return `${fg("warning", "\u2716")} ${fg("dim", `${host}: auth failed`)}`;
	}
	if (state.kind === "unreachable") {
		return `${fg("dim", "\u25cb")} ${fg("dim", `${host}: ${state.error}`)}`;
	}

	const marker = fg("accent", "\u25cf");
	const text = fg(
		"success",
		`${host} · ${state.collections.length} collection${state.collections.length === 1 ? "" : "s"}`,
	);
	return `${marker} ${text}`;
}

/**
 * Probe the configured CalDAV root and report its connection state.
 *
 * Never throws: credentials missing → `unconfigured`, 401 → `auth-failed`, anything
 * else → `unreachable`. Discovery happens at request time, so the reported
 * collections are always fresh.
 */
export async function probeCaldav(): Promise<CalDavState> {
	if (!PI_CALENDAR_URL || !PI_CALENDAR_USER || !PI_CALENDAR_PASSWORD) {
		return { kind: "unconfigured" };
	}

	const url = normalizeBase();
	for (const root of CALENDAR_ROOTS) {
		try {
			const collections = await propfind(`${root}${PI_CALENDAR_USER}/`);
			return { kind: "connected", url, collections };
		} catch (err) {
			const error = err instanceof Error ? err.message : String(err);
			// The first discovery root that fails surfaces its error (it points at the real server).
			if (/401/.test(error)) return { kind: "auth-failed", url };
			return { kind: "unreachable", url, error };
		}
	}
	return { kind: "unconfigured" };
}

/**
 * Wire the CalDAV connection indicator to Pi's status bar.
 *
 * - Discovers the connection at session start (and reports it in the chat window).
 * - Clears the status key when the session ends so it does not linger in the bar.
 */
function installStatusWidget(pi: ExtensionAPI): () => void {
	let ctx: ExtensionContext | undefined;
	let disposed = false;

	async function refresh(): Promise<void> {
		if (disposed) return;
		try {
			const state = await probeCaldav();
			const text = formatCaldavState(state, ctx?.ui.theme ?? defaultTheme());
			ctx?.ui?.setStatus(STATUS_KEY, text.length > 0 ? text : undefined);
		} catch {
			// Best-effort: never break the session over a status update failure.
		}
	}

	pi.on("session_start", async (_event: unknown, eventCtx: ExtensionContext) => {
		ctx = eventCtx;
		void refresh();
	});

	return () => {
		disposed = true;
		if (ctx?.ui) ctx.ui.setStatus(STATUS_KEY, undefined);
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

	// ── Tool: task_create ─────────────────────────────────────────────

	piApi.registerTool({
		name: "task_create",
		label: "Create Calendar Task",
		description:
			"Create a task in a CalDAV task list. Pass `calendar` (name or URL) for the calendar that holds the task lists, plus `summary` and optional `due`/`description`.",
		parameters: Type.Object({
			calendar: Type.String({ description: "The task list name or full href." }),
			summary: Type.String({ description: "Task title." }),
			due: Type.Optional(Type.String({ description: "Due date (ISO-8601)." })),
			description: Type.Optional(Type.String({ description: "Task body." })),
		}),
		async execute(
			_toolCallId: string,
			params: { calendar: string; summary: string; due?: string; description?: string },
			_signal?: AbortSignal,
		) {
			return await executeTaskCreate(params);
		},
	});

	// ── Tool: task_update_status ────────────────────────────────────

	piApi.registerTool({
		name: "task_update_status",
		label: "Update Calendar Task Status",
		description:
		"Mark a CalDAV task done (or change its status). Pass `href` (the task's resource URL) or `calendar` + `uid`, plus the new `status`.",
		parameters: Type.Object({
			href: Type.Optional(
				Type.String({ description: "The task's resource URL, e.g. from `task_list` details." }),
			),
			uid: Type.Optional(Type.String({ description: "Task UID (used with `calendar`)." })),
			calendar: Type.Optional(Type.String({ description: "The task list name or href (used with `uid`)." })),
			status: Type.String({ description: "New status, e.g. COMPLETED." }),
		}),
		async execute(
			_toolCallId: string,
			params: { href?: string; uid?: string; calendar?: string; status: string },
			_signal?: AbortSignal,
		) {
			return await executeTaskUpdateStatus(params);
		},
	});

	// ── Tool: task_delete ─────────────────────────────────────────────

	piApi.registerTool({
		name: "task_delete",
		label: "Delete Calendar Task",
		description:
			"Delete a CalDAV task resource. Pass `href` (the task's resource URL) or `calendar` + `uid`.",
		parameters: Type.Object({
			href: Type.Optional(
				Type.String({ description: "The task's resource URL, e.g. from `task_list` details." }),
			),
			uid: Type.Optional(Type.String({ description: "Task UID (used with `calendar`)." })),
			calendar: Type.Optional(Type.String({ description: "The task list name or href (used with `uid`)." })),
		}),
		async execute(
			_toolCallId: string,
			params: { href?: string; uid?: string; calendar?: string },
			_signal?: AbortSignal,
		) {
			return await executeTaskDelete(params);
		},
	});

	// ── Tool: event_update ────────────────────────────────────────────

	piApi.registerTool({
		name: "event_update",
		label: "Update Calendar Event",
		description:
			"Update a CalDAV event (VEVENT). Pass `href` (the event's resource URL, e.g. from `calendar_list_events` details) or `calendar` + `uid`, plus the fields to change.",
		parameters: Type.Object({
			href: Type.Optional(
				Type.String({ description: "The event's resource URL, e.g. from `calendar_list_events` details." }),
			),
			uid: Type.Optional(Type.String({ description: "Event UID (used with `calendar`)." })),
			calendar: Type.Optional(Type.String({ description: "The calendar name or href (used with `uid`)." })),
			summary: Type.Optional(Type.String({ description: "New event title." })),
			start: Type.Optional(Type.String({ description: "New start time (ISO-8601)." })),
			end: Type.Optional(Type.String({ description: "New end time (ISO-8601)." })),
			description: Type.Optional(Type.String({ description: "New event body." })),
			location: Type.Optional(Type.String({ description: "New event location." })),
			status: Type.Optional(Type.String({ description: "New status, e.g. CONFIRMED." })),
		}),
		async execute(
			_toolCallId: string,
			params: {
				href?: string;
				uid?: string;
				calendar?: string;
				summary?: string;
				start?: string;
				end?: string;
				description?: string;
				location?: string;
				status?: string;
			},
			_signal?: AbortSignal,
		) {
			return await executeEventUpdate(params);
		},
	});

	// ── Tool: event_delete ────────────────────────────────────────────

	piApi.registerTool({
		name: "event_delete",
		label: "Delete Calendar Event",
		description:
			"Delete a CalDAV event resource. Pass `href` (the event's resource URL, e.g. from `calendar_list_events` details) or `calendar` + `uid`.",
		parameters: Type.Object({
			href: Type.Optional(
				Type.String({ description: "The event's resource URL, e.g. from `calendar_list_events` details." }),
			),
			uid: Type.Optional(Type.String({ description: "Event UID (used with `calendar`)." })),
			calendar: Type.Optional(Type.String({ description: "The calendar name or href (used with `uid`)." })),
		}),
		async execute(
			_toolCallId: string,
			params: { href?: string; uid?: string; calendar?: string },
			_signal?: AbortSignal,
		) {
			return await executeEventDelete(params);
		},
	});

	// ── Lifecycle ───────────────────────────────────────────────────────

	const teardown = installStatusWidget(piApi);

	piApi.on("session_start", async (_event: unknown, _ctx: ExtensionContext) => {
		console.log("[caldav] extension loaded");
		if (!PI_CALENDAR_URL || !PI_CALENDAR_USER || !PI_CALENDAR_PASSWORD) {
			console.warn("[caldav] credentials not set — tools will fail at request time");
		}
	});

	piApi.on("session_shutdown", async () => {
		console.log("[caldav] extension unloaded");
		teardown();
	});
}
