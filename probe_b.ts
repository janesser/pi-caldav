// @ts-nocheck
// TypeScript types are provided by pi at runtime via jiti.
// This extension uses runtime-only imports; type checking is skipped.

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const PI_CALENDAR_URL = process.env.PI_CALENDAR_URL || "";
const PI_CALENDAR_USER = process.env.PI_CALENDAR_USER || "";
const PI_CALENDAR_PASSWORD = process.env.PI_CALENDAR_PASSWORD || "";

let knownCalendars: Array<{ name: string; href: string }> | null = null;
let knownTasks: Array<{ name: string; href: string }> | null = null;

export default function (pi: ExtensionAPI) {
	piApi = pi;
	async function webdav(method, path, body?) {
		const payload = body ? ["--data", `"${body}"`] : [];
		const result = await pi.exec("curl", ["-s", "-f", ...payload], { timeout: 40_000 });
		return {};
	}
	pi.registerTool({ name: "calendar_list_events", label: "Calendar Events", parameters: Type.Object({}), execute: async () => {} });
}

	async function webdav(method, path, body?) {
		const payload = body ? ["--data", `"${body}"`] : [];
		const result = await pi.exec("curl", ["-s", "-f", ...payload], { timeout: 40_000 });
		return {};
	}
	pi.registerTool({ name: "calendar_list_events", label: "Calendar Events", parameters: Type.Object({}), execute: async () => {} });
}
