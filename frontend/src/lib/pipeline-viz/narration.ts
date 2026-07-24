// Narration copy for beats (issue #72). Paraglide message functions are
// called HERE, at render time, never at module scope (locale is not set at
// import time — see the warning in $lib/stores).

import { m } from '$lib/paraglide/messages';
import type { Beat } from './types';

const pct = (v: unknown): number =>
	typeof v === 'number' ? Math.round(v * 100) : 0;

export function beatText(beat: Beat): string {
	const d = beat.data;
	switch (beat.kind) {
		case 'arrival':
			return m.replay_beat_arrival();
		case 'policy': {
			const disposition = d.deterministic_disposition;
			return disposition
				? m.replay_beat_policy({ disposition: String(disposition) })
				: m.replay_beat_policy_full();
		}
		case 'supervisor':
			return m.replay_beat_supervisor({ action: String(d.next_action ?? '?') });
		case 'worker_started':
			return m.replay_beat_worker_started({ worker: String(d.worker ?? '?') });
		case 'worker_result':
			return d.ok === false
				? m.replay_beat_worker_failed({ worker: String(d.worker ?? '?') })
				: m.replay_beat_worker_done({ worker: String(d.worker ?? '?') });
		case 'verdict':
			return m.replay_beat_verdict({
				decision: String(d.decision ?? '?'),
				pct: pct(d.confidence)
			});
		case 'guard': {
			const effect = String(d.effect ?? 'pass');
			if (effect === 'override')
				return m.replay_beat_guard_override({
					from: String(d.decision_in ?? '?'),
					to: String(d.decision_out ?? '?')
				});
			if (effect === 'interrupt') return m.replay_beat_guard_interrupt();
			return m.replay_beat_guard_pass({ stage: String(d.stage ?? '?') });
		}
		case 'closed':
			return m.replay_beat_closed({ path: String(d.path ?? '?') });
		case 'human_requested':
			return m.replay_beat_human_requested();
		case 'human_decision':
			return m.replay_beat_human_decision({ decision: String(d.decision ?? '?') });
		case 'reopened':
			return m.replay_beat_reopened();
		default:
			return beat.eventKind.replace(/_/g, ' ');
	}
}

export function beatDetail(beat: Beat): string | null {
	const d = beat.data;
	switch (beat.kind) {
		case 'policy': {
			const fired = Array.isArray(d.vetoes_fired) ? d.vetoes_fired : [];
			if (fired.length > 0)
				return m.replay_detail_vetoes_fired({ vetoes: fired.map(String).join(', ') });
			return null;
		}
		case 'supervisor':
			return d.action_reasoning ? String(d.action_reasoning) : null;
		case 'worker_result':
			return d.summary ? String(d.summary) : null;
		case 'verdict':
			return d.threat_assessment ? String(d.threat_assessment) : null;
		case 'guard': {
			const fired = Array.isArray(d.fired) ? d.fired : [];
			return fired.length > 0 ? fired.map(String).join(', ') : null;
		}
		case 'closed':
			return d.reason ? String(d.reason) : null;
		case 'human_requested':
			return d.reason ? String(d.reason) : null;
		default:
			return null;
	}
}
