<script lang="ts">
	import { m } from '$lib/paraglide/messages';
	import type { EdgeId, EdgeState, NodeId, NodeState } from './types';

	export let nodes: Partial<Record<NodeId, NodeState>> = {};
	export let edges: Partial<Record<EdgeId, EdgeState>> = {};

	interface NodeGeom {
		id: NodeId;
		x: number;
		y: number;
		w: number;
		h: number;
		label: () => string;
		role: () => string;
	}

	// Fixed geography: identical for every investigation — viewers learn the
	// map once. Beats vary; the stage never does.
	const NODES: NodeGeom[] = [
		{ id: 'alert', x: 16, y: 182, w: 118, h: 56, label: () => m.replay_node_alert(), role: () => m.replay_role_alert() },
		{ id: 'gate', x: 178, y: 182, w: 132, h: 56, label: () => m.replay_node_gate(), role: () => m.replay_role_gate() },
		{ id: 'sup', x: 372, y: 182, w: 148, h: 56, label: () => m.replay_node_sup(), role: () => m.replay_role_sup() },
		{ id: 'verdict', x: 640, y: 182, w: 120, h: 56, label: () => m.replay_node_verdict(), role: () => m.replay_role_verdict() },
		{ id: 'guard', x: 804, y: 182, w: 104, h: 56, label: () => m.replay_node_guard(), role: () => m.replay_role_guard() },
		{ id: 'wazuh', x: 246, y: 40, w: 104, h: 44, label: () => 'WAZUH', role: () => m.replay_role_worker_logs() },
		{ id: 'cortex', x: 366, y: 40, w: 104, h: 44, label: () => 'CORTEX', role: () => m.replay_role_worker_enrich() },
		{ id: 'misp', x: 486, y: 40, w: 96, h: 44, label: () => 'MISP', role: () => m.replay_role_worker_ti() },
		{ id: 'authz', x: 598, y: 40, w: 110, h: 44, label: () => m.replay_node_authz(), role: () => m.replay_role_worker_authz() },
		{ id: 'thehive', x: 724, y: 40, w: 100, h: 44, label: () => 'THEHIVE', role: () => m.replay_role_worker_case() },
		{ id: 'human', x: 952, y: 96, w: 112, h: 52, label: () => m.replay_node_human(), role: () => m.replay_role_human() },
		{ id: 'close', x: 952, y: 272, w: 112, h: 52, label: () => m.replay_node_close(), role: () => m.replay_role_close() }
	];

	const EDGES: { id: EdgeId; d: string }[] = [
		{ id: 'alert-gate', d: 'M134 210 H 178' },
		{ id: 'gate-sup', d: 'M310 210 H 372' },
		{ id: 'sup-wazuh', d: 'M410 182 C 410 130, 298 116, 298 84' },
		{ id: 'sup-cortex', d: 'M436 182 C 436 136, 418 122, 418 84' },
		{ id: 'sup-misp', d: 'M462 182 C 468 132, 534 122, 534 84' },
		{ id: 'sup-authz', d: 'M488 182 C 500 132, 653 126, 653 84' },
		{ id: 'sup-verdict', d: 'M520 210 H 640' },
		{ id: 'verdict-guard', d: 'M760 210 H 804' },
		{ id: 'guard-human', d: 'M908 194 C 940 180, 940 160, 952 148' },
		{ id: 'guard-close', d: 'M908 226 C 940 240, 940 260, 952 272' },
		{ id: 'gate-close', d: 'M244 238 C 244 370, 900 380, 964 306' },
		{ id: 'human-close', d: 'M1008 148 C 1008 190, 1008 230, 1008 272' }
	];
</script>

<div class="map-wrap" data-testid="pipeline-map">
	<svg viewBox="0 0 1080 420" role="img" aria-label={m.replay_map_aria()}>
		{#each EDGES as edge (edge.id)}
			<path class="edge {edges[edge.id] ?? 'idle'}" d={edge.d} data-edge={edge.id} />
		{/each}
		<text class="edgelabel" x="430" y="366">{m.replay_fast_path_label()}</text>
		{#each NODES as node (node.id)}
			<g class="node {nodes[node.id] ?? 'idle'}" data-node={node.id} data-state={nodes[node.id] ?? 'idle'}>
				<rect x={node.x} y={node.y} width={node.w} height={node.h} rx="7" />
				<text class="label" x={node.x + node.w / 2} y={node.y + node.h / 2 - 4} text-anchor="middle">
					{node.label()}
				</text>
				<text class="role" x={node.x + node.w / 2} y={node.y + node.h / 2 + 12} text-anchor="middle">
					{node.role()}
				</text>
			</g>
		{/each}
	</svg>
</div>

<style>
	.map-wrap {
		overflow-x: auto;
	}
	svg {
		display: block;
		min-width: 720px;
		width: 100%;
		height: auto;
	}
	.edge {
		stroke: rgb(var(--color-surface-400) / 0.35);
		stroke-width: 2;
		fill: none;
	}
	.edge.active {
		stroke: rgb(var(--color-primary-500));
		stroke-dasharray: 7 7;
		animation: flow 0.7s linear infinite;
	}
	.edge.done {
		stroke: rgb(var(--color-primary-500) / 0.55);
	}
	.edge.taken-good {
		stroke: rgb(var(--color-success-500));
	}
	.edge.taken-warn {
		stroke: rgb(var(--color-warning-500));
	}
	@keyframes flow {
		to {
			stroke-dashoffset: -14;
		}
	}
	.node rect {
		fill: rgb(var(--color-surface-700) / 0.6);
		stroke: rgb(var(--color-surface-400) / 0.4);
		stroke-width: 1.4;
		transition: stroke 0.3s, fill 0.3s;
	}
	.node text {
		fill: rgb(var(--color-surface-300));
		font-family: ui-monospace, 'SF Mono', Menlo, monospace;
		font-size: 11.5px;
		letter-spacing: 0.03em;
	}
	.node .role {
		font-size: 9px;
		letter-spacing: 0.12em;
		fill: rgb(var(--color-surface-400));
	}
	.node.active rect {
		stroke: rgb(var(--color-primary-500));
		fill: rgb(var(--color-primary-500) / 0.12);
	}
	.node.active text {
		fill: rgb(var(--color-primary-300));
	}
	.node.done rect {
		stroke: rgb(var(--color-primary-500) / 0.5);
	}
	.node.pass rect {
		stroke: rgb(var(--color-success-500));
		fill: rgb(var(--color-success-500) / 0.12);
	}
	.node.pass text {
		fill: rgb(var(--color-success-400));
	}
	.node.veto rect {
		stroke: rgb(var(--color-error-500));
		fill: rgb(var(--color-error-500) / 0.14);
	}
	.node.veto text {
		fill: rgb(var(--color-error-400));
	}
	.node.warn rect {
		stroke: rgb(var(--color-warning-500));
		fill: rgb(var(--color-warning-500) / 0.12);
	}
	.node.warn text {
		fill: rgb(var(--color-warning-400));
	}
	.edgelabel {
		fill: rgb(var(--color-surface-400));
		font-family: ui-monospace, 'SF Mono', Menlo, monospace;
		font-size: 9.5px;
		letter-spacing: 0.06em;
	}
	@media (prefers-reduced-motion: reduce) {
		.edge.active {
			animation: none;
			stroke-dasharray: none;
		}
		.node rect {
			transition: none;
		}
	}
</style>
