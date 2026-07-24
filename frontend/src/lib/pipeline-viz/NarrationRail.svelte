<script lang="ts">
	import { afterUpdate } from 'svelte';
	import { m } from '$lib/paraglide/messages';
	import { beatDetail, beatText } from './narration';
	import type { Beat } from './types';

	export let beats: Beat[] = [];
	export let visibleCount = 0;

	let rail: HTMLDivElement | null = null;
	afterUpdate(() => {
		if (rail) rail.scrollTop = rail.scrollHeight;
	});

	const fmtOffset = (ms: number): string => `+${(ms / 1000).toFixed(1)}s`;
</script>

<div class="card p-4" data-testid="narration-rail">
	<h3 class="h4 mb-2">{m.replay_narration_title()}</h3>
	<div class="rail" bind:this={rail}>
		{#if visibleCount === 0}
			<p class="text-sm opacity-60">{m.replay_no_beats_yet()}</p>
		{/if}
		{#each beats.slice(0, visibleCount) as beat (beat.seq)}
			<div class="beat tone-{beat.tone}">
				<span class="t">{fmtOffset(beat.tReal)}</span>
				<span class="txt">
					{beatText(beat)}
					{#if beatDetail(beat)}
						<small>{beatDetail(beat)}</small>
					{/if}
				</span>
			</div>
		{/each}
	</div>
</div>

<style>
	.rail {
		display: flex;
		flex-direction: column;
		max-height: 20rem;
		overflow-y: auto;
	}
	.beat {
		display: grid;
		grid-template-columns: 3.5rem 1fr;
		gap: 0.6rem;
		padding: 0.35rem 0;
		border-bottom: 1px solid rgb(var(--color-surface-500) / 0.2);
		font-size: 0.8rem;
	}
	.t {
		font-family: ui-monospace, Menlo, monospace;
		font-size: 0.7rem;
		opacity: 0.55;
		font-variant-numeric: tabular-nums;
		padding-top: 1px;
	}
	.txt small {
		display: block;
		opacity: 0.65;
		font-size: 0.72rem;
		margin-top: 1px;
	}
	.tone-good .txt {
		color: rgb(var(--color-success-400));
	}
	.tone-warn .txt {
		color: rgb(var(--color-warning-400));
	}
	.tone-bad .txt {
		color: rgb(var(--color-error-400));
	}
</style>
