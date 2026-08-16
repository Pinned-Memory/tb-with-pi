/**
 * Capture every provider request pi sends, for offline replay benchmarking.
 *
 * Enabled only when PI_CAPTURE_DIR is set, so normal sessions pay nothing:
 *
 *   PI_CAPTURE_DIR=~/captures/run1 pi -p "/solve <task>"
 *
 * One JSONL file per process (parent and each subagent are separate pi
 * processes; a shared file would interleave concurrent appends). Each line:
 * {t: epoch-ms, pid, payload: <the exact JSON body sent to the provider>}.
 * Merge and order by `t` at replay time.
 */
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
	// In a Harbor sandbox /logs/agent is the host-mounted per-trial log dir, so
	// captures written there survive the container without extra plumbing.
	const dir =
		process.env.PI_CAPTURE_DIR ??
		(existsSync("/logs/agent") ? "/logs/agent/pi-capture" : undefined);
	if (!dir) return;
	mkdirSync(dir, { recursive: true });
	const file = join(dir, `requests-${process.pid}.jsonl`);

	pi.on("before_provider_request", (event) => {
		appendFileSync(
			file,
			`${JSON.stringify({ t: Date.now(), pid: process.pid, payload: event.payload })}\n`,
			"utf8",
		);
	});

}
