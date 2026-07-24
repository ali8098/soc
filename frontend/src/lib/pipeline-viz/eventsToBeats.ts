// IR event feed → Beats (issue #72). Pure: no DOM, no i18n calls, no Date.now.
// Input must be the cursor feed (seq ascending); unknown kinds become generic
// beats rather than being dropped — the recorder never silently omits.

import type { InvestigationTimelineEvent } from '$lib/api/client';
import type { Beat, BeatKind, BeatTone } from './types';

const KIND_MAP: Record<string, BeatKind> = {
	alert_ingested: 'arrival',
	policy_resolved: 'policy',
	supervisor_decision: 'supervisor',
	worker_started: 'worker_started',
	worker_result: 'worker_result',
	verdict_rendered: 'verdict',
	guard_evaluated: 'guard',
	auto_closed: 'closed',
	human_review_requested: 'human_requested',
	human_decision: 'human_decision',
	reopened: 'reopened'
};

function toneFor(kind: BeatKind, data: Record<string, unknown>): BeatTone {
	switch (kind) {
		case 'guard': {
			const effect = String(data.effect ?? '');
			if (effect === 'override') return 'bad';
			if (effect === 'interrupt') return 'warn';
			return 'good';
		}
		case 'closed':
			return 'good';
		case 'human_requested':
			return 'warn';
		case 'worker_result':
			return data.ok === false ? 'bad' : 'neutral';
		case 'verdict':
			return 'good';
		default:
			return 'neutral';
	}
}

export function eventsToBeats(events: InvestigationTimelineEvent[]): Beat[] {
	const withSeq = events
		.filter((e) => typeof e.seq === 'number')
		.sort((a, b) => (a.seq as number) - (b.seq as number));
	if (withSeq.length === 0) return [];
	const t0 = Date.parse(withSeq[0].timestamp);
	return withSeq.map((e) => {
		const kind = KIND_MAP[e.event_type] ?? 'other';
		const data = (e.data ?? {}) as Record<string, unknown>;
		return {
			seq: e.seq as number,
			at: e.timestamp,
			tReal: Math.max(0, Date.parse(e.timestamp) - t0),
			kind,
			tone: toneFor(kind, data),
			data,
			eventKind: e.event_type
		};
	});
}

/** Worker payload name → map node id. */
export function workerNode(
	data: Record<string, unknown>
): 'wazuh' | 'cortex' | 'misp' | 'authz' | 'thehive' | null {
	switch (String(data.worker ?? '')) {
		case 'wazuh':
			return 'wazuh';
		case 'cortex':
			return 'cortex';
		case 'misp':
			return 'misp';
		case 'authorization_context':
			return 'authz';
		case 'thehive':
			return 'thehive';
		default:
			return null;
	}
}
