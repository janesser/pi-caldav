/**
 * Load `extensions/caldav.ts` and run one of its tools, for use by the test
 * suite (see `tests/test_caldav.py`).
 *
 * Usage:
 *   node --experimental-strip-types extension_runner.js [options]
 *
 * Options (all optional):
 *   --mode=unit|int|tools    unit = stub the exec layer, int = run real curl,
 *                            tools = print registered tool names only
 *   --tool=<name>            tool to execute (default: none)
 *   --params={"..."}         JSON tool parameters
 *   --fixture=<name>         fixture body used by the exec stub (unit mode)
 *   --discovery-fixture=<n>  fixture body used for discovery PROPFIND calls
 *                            (default: propfind_collection.xml)
 *   --status=401             status code appended by the exec stub (default 207)
 *   --fail                   exec stub reports a failed curl call (exit 7)
 *   --killed                 exec stub reports a timed out curl call (killed: true)
 *   --statuses=207,500       per-call statuses the stub cycles through (default [207])
 */
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL("../", import.meta.url));
const args = process.argv.slice(2);
const option = (name) => {
	for (const arg of args) {
		const m = new RegExp(`^--${name}=(.+)$`).exec(arg);
		if (m) return decodeURIComponent(m[1]);
	}
	return undefined;
};

const mode = option("mode") || "unit";
const toolName = option("tool") || "";
const params = JSON.parse(option("params") || "{}");
const fixture = option("fixture");
const discoveryFixture = option("discovery-fixture") || "propfind_collection.xml";
const status = parseInt(option("status") || "207", 10);
const fail = args.includes("--fail");
const killed = args.includes("--killed");
// Per-call statuses, applied in order to each exec call (wrapping around).
const statuses = option("statuses")
	? option("statuses").split(",").map((s) => parseInt(s.trim(), 10))
	: [status];

const tools = [];
const execCalls = [];
let callIndex = 0;

function stubExec(command, args, options) {
	execCalls.push({ command, args, options });
	if (mode === "unit") {
		const isDiscovery = args.includes("PROPFIND");
		const name = isDiscovery ? discoveryFixture : fixture;
		const body = name
			? readFileSync(`${ROOT}/tests/fixtures/${name}`).toString()
			: "";
		// The status of this call comes from the per-call list, wrapped around.
		const callStatus = statuses[callIndex % statuses.length];
		callIndex++;
		return Promise.resolve({
			stdout: `${body}\n${callStatus}`,
			stderr: fail ? "curl: (7) Failed to connect" : "",
			code: fail ? 7 : 0,
			killed: killed,
		});
	}
	// Integration mode: let the extension's own curl call reach the mock server.
	// `spawnSync` passes the args as an argv array (no shell), so `-w \n%{http_code}`
	// and `--data-raw <body>` survive intact.
	const result = spawnSync(command, args, {
		env: process.env,
		timeout: options?.timeout ?? 40_000,
		stdio: ["ignore", "pipe", "pipe"],
	});
	return Promise.resolve({
		stdout: result.stdout ? result.stdout.toString() : "",
		stderr: result.stderr ? result.stderr.toString() : "",
		code: result.status ?? -1,
		killed: result.status === null,
	});
}

// Load the extension with a stubbed `pi` API (same shape pi-agent provides).
const extension = await import(`${ROOT}/extensions/caldav.ts`);
extension.default({
	registerTool: (tool) => tools.push(tool),
	exec: stubExec,
	on: () => {},
});

if (mode === "tools") {
	console.log(JSON.stringify({ tools: tools.map((t) => t.name) }));
	process.exit(0);
}

const tool = tools.find((t) => t.name === toolName);
if (!tool) {
	console.log(JSON.stringify({ error: `unknown tool: ${toolName}` }));
	process.exit(1);
}

try {
	const result = await tool.execute("test-call-id", params, undefined);
	console.log(JSON.stringify({ execCalls, result }).replace(/\n/g, ""));
} catch (err) {
	console.log(JSON.stringify({ execCalls, error: err.message }));
}
