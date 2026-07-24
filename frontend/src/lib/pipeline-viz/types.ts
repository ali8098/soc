// Flight-recorder core types (issue #72).
// Rendering is a pure function of (beats, t): the adapter turns the IR event
// feed into Beats, the scene reducer folds beats up to t, components paint.

export type NodeId =
	| 'alert'
	| 'gate'
	| 'sup'
	| 'wazuh'
	| 'cortex'
	| 'misp'
	| 'authz'
	| 'thehive'
	| 'verdict'
	| 'guard'
	| 'human'
	| 'close';

export type EdgeId =
	| 'alert-gate'
	| 'gate-sup'
	| 'sup-wazuh'
	| 'sup-cortex'
	| 'sup-misp'
	| 'sup-authz'
	| 'sup-verdict'
	| 'verdict-guard'
	| 'guard-human'
	| 'guard-close'
	| 'gate-close'
	| 'human-close';

export type NodeState = 'idle' | 'active' | 'done' | 'pass' | 'veto' | 'warn';
export type EdgeState = 'idle' | 'active' | 'done' | 'taken-good' | 'taken-warn';

export type BeatKind =
	| 'arrival'
	| 'policy'
	| 'supervisor'
	| 'worker_started'
	| 'worker_result'
	| 'verdict'
	| 'guard'
	| 'closed'
	| 'human_requested'
	| 'human_decision'
	| 'reopened'
	| 'other';

export type BeatTone = 'neutral' | 'good' | 'warn' | 'bad';

// Every Beat is backed by a persisted event by construction — the adapter
// only consumes the IR cursor feed (the honesty bar, made structural).
export interface Beat {
	seq: number;
	/** ISO timestamp of the persisted event (server clock). */
	at: string;
	/** Milliseconds since the first beat, real time. */
	tReal: number;
	kind: BeatKind;
	tone: BeatTone;
	/** Raw event payload for detail rendering. */
	data: Record<string, unknown>;
	/** Original event kind string (for generic beats). */
	eventKind: string;
}

export interface VerdictView {
	decision: string | null;
	confidence: number | null;
	keyEvidence: string[];
	gaps: string[];
	alternatives: string[];
	recommendation: string | null;
	flippedTo: string | null; // guard override target, if any
}

export interface Scene {
	nodes: Partial<Record<NodeId, NodeState>>;
	edges: Partial<Record<EdgeId, EdgeState>>;
	verdict: VerdictView | null;
}

// Wire DTOs live with the API client (#72 quality pass: the API layer must
// not depend on the visualization module). Re-exported here so pipeline-viz
// internals keep a single types import.
export type {
	FleetArrival,
	FleetDay,
	FleetDot,
	FleetLive,
	FleetVetoRow
} from '$lib/api/client';
