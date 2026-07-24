<script lang="ts">
	import { m } from '$lib/paraglide/messages';
	import type { Timeline } from './timeline';

	export let timeline: Timeline;
	export let beatTimes: number[] = [];
	export let realSpanMs = 0;
	export let live = false;

	$: state = $timeline;

	const fmt = (ms: number): string => {
		const s = ms / 1000;
		return `0:${String(Math.floor(s)).padStart(2, '0')}.${Math.floor((s % 1) * 10)}`;
	};

	function stepBack() {
		timeline.pause();
		const prev = [...beatTimes].reverse().find((t) => t < state.t);
		timeline.seek(prev ?? 0);
	}
	function stepFwd() {
		timeline.pause();
		const next = beatTimes.find((t) => t > state.t);
		timeline.seek(next ?? state.duration);
	}
	function onScrub(e: Event) {
		timeline.pause();
		timeline.seek(Number((e.target as HTMLInputElement).value));
	}
</script>

<div class="flex items-center gap-2 flex-wrap" data-testid="replay-transport">
	<button
		type="button"
		class="btn btn-sm variant-soft-primary font-mono"
		on:click={() => timeline.toggle()}
		aria-label={state.playing ? m.replay_pause() : m.replay_play()}
		data-testid="replay-play"
	>
		{state.playing ? '❚❚' : '▶'}
	</button>
	<button
		type="button"
		class="btn btn-sm variant-soft"
		on:click={() => timeline.restart()}
		aria-label={m.replay_restart()}
	>
		↺
	</button>
	<button type="button" class="btn btn-sm variant-soft" on:click={stepBack} aria-label={m.replay_step_back()}>
		⏮
	</button>
	<button type="button" class="btn btn-sm variant-soft" on:click={stepFwd} aria-label={m.replay_step_fwd()}>
		⏭
	</button>
	<input
		type="range"
		class="flex-1 min-w-[8rem] accent-primary-500"
		min="0"
		max={state.duration}
		step="10"
		value={state.t}
		on:input={onScrub}
		aria-label={m.replay_scrub()}
	/>
	<span class="text-xs font-mono opacity-70 tabular-nums whitespace-nowrap">
		{fmt(state.t)} / {fmt(state.duration)}
	</span>
	{#if live}
		<span class="badge variant-soft-error font-mono text-[0.65rem]" data-testid="replay-live-chip">
			{m.replay_live()}
		</span>
	{/if}
</div>
{#if realSpanMs > 0}
	<p class="text-[0.68rem] font-mono opacity-50 mt-1">
		{m.replay_compression({
			real: (realSpanMs / 1000).toFixed(1),
			play: (state.duration / 1000).toFixed(1)
		})}
	</p>
{/if}
