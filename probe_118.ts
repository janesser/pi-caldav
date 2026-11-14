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
export default function (pi: any) { return {} }
