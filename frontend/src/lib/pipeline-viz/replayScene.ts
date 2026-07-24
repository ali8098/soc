// Scene reducer (issue #72): fold beats[0..i] into node/edge states + the
// verdict panel view. Pure — seeking is free and screenshots deterministic.

import { workerNode } from './eventsToBeats';
import type { Beat, EdgeId, NodeId, Scene, VerdictView } from './types';

function strOrNull(v: unknown): string | null {
	return v === undefined || v === null ? null : String(v);
}

function strList(v: unknown): string[] {
	return Array.isArray(v) ? v.map(String) : [];
}

export function reduceScene(beats: Beat[], lastIndex: number): Scene {
	const nodes: Scene['nodes'] = {};
	const edges: Scene['edges'] = {};
	let verdict: VerdictView | null = null;
	let status: Scene['status'] = 'triaging';

	const doneNode = (id: NodeId) => {
		if (nodes[id] === 'active') nodes[id] = 'done';
	};
	const takeEdge = (id: EdgeId, state: Scene['edges'][EdgeId] = 'done') => {
		edges[id] = state;
	};

	for (let i = 0; i <= lastIndex && i < beats.length; i++) {
		const b = beats[i];
		const d = b.data;
		switch (b.kind) {
			case 'arrival':
				nodes.alert = 'active';
				break;
			case 'policy':
				doneNode('alert');
				takeEdge('alert-gate');
				nodes.gate = 'active';
				break;
			case 'supervisor':
				doneNode('gate');
				takeEdge('gate-sup');
				nodes.sup = 'active';
				break;
			case 'worker_started': {
				const w = workerNode(d);
				if (w) {
					nodes[w] = 'active';
					if (w !== 'thehive') takeEdge(`sup-${w}` as EdgeId, 'active');
				}
				break;
			}
			case 'worker_result': {
				const w = workerNode(d);
				if (w) {
					nodes[w] = d.ok === false ? 'warn' : 'done';
					if (w !== 'thehive') takeEdge(`sup-${w}` as EdgeId, 'done');
				}
				break;
			}
			case 'verdict':
				doneNode('sup');
				takeEdge('sup-verdict');
				nodes.verdict = 'active';
				verdict = {
					decision: strOrNull(d.decision),
					confidence: typeof d.confidence === 'number' ? d.confidence : null,
					keyEvidence: strList(d.key_evidence),
					gaps: strList(d.gaps_in_evidence),
					alternatives: strList(d.alternative_explanations),
					recommendation: strOrNull(d.recommendation),
					flippedTo: null
				};
				break;
			case 'guard': {
				const stage = String(d.stage ?? '');
				const effect = String(d.effect ?? 'pass');
				if (stage === 'operational' || stage === 'ingest') {
					// Fast-path ruling happens at the gate, not the guard node.
					nodes.gate = effect === 'pass' ? 'pass' : 'veto';
					break;
				}
				doneNode('verdict');
				takeEdge('verdict-guard');
				if (effect === 'pass') {
					nodes.guard = 'pass';
				} else if (effect === 'interrupt') {
					nodes.guard = 'warn';
				} else {
					nodes.guard = 'veto';
					if (verdict !== null) {
						// Locally-built object; in-place update keeps TS inference simple.
						verdict.flippedTo = strOrNull(d.decision_out);
					}
				}
				break;
			}
			case 'closed': {
				const path = String(d.path ?? '');
				if (path.startsWith('ingest') || path === 'operational') {
					nodes.gate = 'pass';
					takeEdge('gate-close', 'taken-good');
				} else {
					if (nodes.guard === undefined || nodes.guard === 'active') nodes.guard = 'pass';
					takeEdge('guard-close', 'taken-good');
				}
				nodes.close = 'pass';
				status = 'auto_closed';
				break;
			}
			case 'human_requested':
				nodes.human = 'warn';
				takeEdge('guard-human', 'taken-warn');
				status = 'escalated';
				break;
			case 'human_decision': {
				const decision = String(d.decision ?? '');
				nodes.human = decision === 'approve' ? 'done' : 'warn';
				if (decision === 'reject') {
					nodes.close = 'pass';
					takeEdge('human-close', 'taken-good');
					status = 'auto_closed';
				}
				break;
			}
			default:
				break;
		}
	}

	return { nodes, edges, verdict, status, lastBeatIndex: lastIndex };
}
