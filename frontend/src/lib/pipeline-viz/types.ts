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

export interface Beat {
	seq: number;
	/** ISO timestamp of the persisted event (server clock). */
	at: string;
	/** Milliseconds since the first beat, real time. */
	tReal: number;
	kind: BeatKind;
	tone: BeatTone;
	/** Every rendered beat is backed by a persisted event. */
	support: 'persisted';
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
	status: 'triaging' | 'auto_closed' | 'escalated';
	lastBeatIndex: number;
}

export interface FleetDot {
	alert_id: string;
	investigation_id: string | null;
	first_event_at: string;
	closed_at: string | null;
	path: string | null;
	outcome: 'closed' | 'human' | 'closed_unrecorded' | 'open';
	veto: boolean;
}

export interface FleetVetoRow {
	investigation_id: string;
	at: string;
	stage: string | null;
	fired: string[];
}

export interface FleetDay {
	date: string;
	tz: string;
	server_now: string;
	window_start: string;
	window_end: string;
	ingested: number;
	closed_ingest_memoized: number;
	closed_ingest_rules: number;
	closed_operational: number;
	closed_reasoning: number;
	escalated: number;
	guard_vetoes: number;
	still_open: number;
	ingest_histogram: number[];
	dollars_used: number;
	tokens_used: number;
	sample_rate: number;
	dots: FleetDot[];
	recent_vetoes: FleetVetoRow[];
}
