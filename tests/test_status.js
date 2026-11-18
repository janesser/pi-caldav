/**
 * Test `formatCaldavState` — the formatter behind the connection indicator that the
 * extension reports in the chat window's status bar (see `extensions/caldav.ts`).
 *
 * It is a pure function: no pi runtime needed, only that the extension loads.
 * Colors are verified against a stub theme; plain output holds for environments
 * without a TUI theme.
 *
 * Usage:
 *   node --experimental-strip-types tests/test_status.js
 */

import { formatCaldavState } from "../extensions/caldav.ts";

const theme = { fg: (color, text) => `[${color}]${text}` };
const plainTheme = { fg: (_color, text) => text };

// The shapes the extension's `CalDavState` can take, plus what is expected out.
const cases = [
	[
		{ kind: "unconfigured" },
		"[warning]\u2716 no credentials",
	],
	[
		{ kind: "auth-failed", url: "https://nextcloud.example.com/" },
		"[warning]\u2716 [dim]nextcloud.example.com: auth failed",
	],
	[
		{
			kind: "unreachable",
			url: "https://nextcloud.example.com/",
			error: "WebDAV PROPFIND failed (curl exit code 7)",
		},
		"[dim]\u25cb [dim]nextcloud.example.com: WebDAV PROPFIND failed (curl exit code 7)",
	],
	[
		{
			kind: "connected",
			url: "https://nextcloud.example.com/",
			collections: [
				{ displayname: "JanEsser", href: "/remote.php/caldav/calendars/jan/", resourcetype: "collection" },
				{ displayname: "Family", href: "/remote.php/caldav/calendars/jan/family/", resourcetype: "collection" },
			],
		},
		"[accent]\u25cf [success]nextcloud.example.com \u00b7 2 collections",
	],
	[
		{
			kind: "connected",
			url: "https://nextcloud.example.com/",
			collections: [{ displayname: "JanEsser", href: "/remote.php/caldav/calendars/jan/", resourcetype: "collection" }],
		},
		"[accent]\u25cf [success]nextcloud.example.com \u00b7 1 collection",
	],
];

let failed = 0;
for (const [state, expected] of cases) {
	const withTheme = formatCaldavState(state, theme);
	const plain = formatCaldavState(state, plainTheme);
	if (withTheme !== expected) {
		console.log(`FAIL ${JSON.stringify(state)}\n  expected: ${expected}\n  actual:   ${withTheme}`);
		failed++;
	}
	console.log(`${withTheme === expected ? "ok" : "!!"} color | plain: ${plain}`);
}

process.exit(failed ? 1 : 0);
