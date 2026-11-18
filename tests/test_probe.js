/**
 * Test `probeCaldav` — the connection probe behind the status bar widget (see
 * `extensions/caldav.ts`).
 *
 * The extension's own curl call is stubbed, so no HTTP and no live credentials are
 * needed: pinned (non-live) env credentials make the state independent of this machine.
 *
 * Usage:
 *   PI_CALENDAR_URL=http://127.0.0.1/ PI_CALENDAR_USER=testuser \
 *   PI_CALENDAR_PASSWORD=testpassword node --experimental-strip-types tests/test_probe.js
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Pinned (non-live) credentials — the extension resolves them at load time, so they
// must be set before the module is imported.
process.env.PI_CALENDAR_URL = "http://127.0.0.1/";
process.env.PI_CALENDAR_USER = "testuser";
process.env.PI_CALENDAR_PASSWORD = "testpassword";

const ROOT = fileURLToPath(new URL("../", import.meta.url));
const fixture = (name) => readFileSync(`${ROOT}/tests/fixtures/${name}`).toString();

// The discovery fixture enumerates two collections (207 Multi-Status).
const DISCOVERY_OK = `${fixture("propfind_collection.xml")}\n207`;
// 401 Unauthorized.
const ERROR_401 = `${fixture("response_401.xml")}\n401`;

let failed = 0;

/** Run one state assertion, loading the extension with a stubbed `pi.exec`. */
async function run(name, execStub, assert) {
	const extension = await import("../extensions/caldav.ts");
	extension.default({ registerTool: () => undefined, exec: execStub, on: () => undefined });
	const state = await extension.probeCaldav();
	console.log(`${name.padEnd(12)}: ${JSON.stringify(state)}`);
	assert(state);
}

// --- connected: the discovery fixture enumerates two collections -----------------------
await run("connected", (command, args) => {
	return Promise.resolve({
		stdout: /PROPFIND/.test(args.join(" ")) ? DISCOVERY_OK : "",
		stderr: "",
		code: 0,
		killed: false,
	});
}, (state) => {
	const collections = state.kind === "connected" ? state.collections : [];
	if (state.kind !== "connected" || collections.length !== 2) {
		console.log("FAIL expected `connected` with 2 collections");
		failed++;
	} else if (collections.map((c) => c.displayname).join(",") !== "JanEsser,Familie-Esser") {
		console.log(`FAIL collection names: ${collections.map((c) => c.displayname).join(",")}`);
		failed++;
	}
});

// --- auth failed: the server answers 401 ----------------------------------------------
await run("auth-failed", () => {
	return Promise.resolve({
		stdout: ERROR_401,
		stderr: "",
		code: 0,
		killed: false,
	});
}, (state) => {
	if (state.kind !== "auth-failed") {
		console.log("FAIL expected `auth-failed`");
		failed++;
	}
});

// --- unreachable: the curl call fails -------------------------------------------------
await run("unreachable", () => {
	return Promise.resolve({
		stdout: "",
		stderr: "curl: (7) Failed to connect",
		code: 7,
		killed: false,
	});
}, (state) => {
	if (state.kind !== "unreachable" || !/Failed to connect/.test(state.error)) {
		console.log("FAIL expected `unreachable` with the curl error");
		failed++;
	}
});

process.exit(failed ? 1 : 0);
