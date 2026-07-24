<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy, onMount } from 'svelte';
	import { api, type InvestigationTimelineEvent } from '$lib/api/client';
	import { m } from '$lib/paraglide/messages';
	import { eventsToBeats } from './eventsToBeats';
	import NarrationRail from './NarrationRail.svelte';
	import PipelineMap from './PipelineMap.svelte';
	import { reduceScene } from './replayScene';
	import { beatIndexAt, buildTimeScale } from './timeScale';
	import { createTimeline } from './timeline';
	import Transport from './Transport.svelte';
	import type { Beat } from './types';
	import VerdictPanel from './VerdictPanel.svelte';

	export let investigationId: string;
	/** Active investigations poll the cursor feed — the live head. */
	export let isActive = false;

	const LIVE_POLL_MS = 5000;

	let events: InvestigationTimelineEvent[] = [];
	let beats: Beat[] = [];
	let loading = true;
	let error: string | null = null;
	let nextAfterSeq = 0;
	let pollHandle: ReturnType<typeof setInterval> | null = null;

	const timeline = createTimeline(0);

	$: scale = buildTimeScale(beats);
	$: timeline.setDuration(scale.duration);
	$: t = $timeline.t;
	$: currentIndex = beatIndexAt(scale, t);
	$: scene = reduceScene(beats, currentIndex);

	async function fetchPage(initial: boolean) {
		try {
			const resp = await api.investigations.getEventsCursor(investigationId, nextAfterSeq);
			if (resp.events.length > 0) {
				events = [...events, ...resp.events];
				beats = eventsToBeats(events);
			}
			if (typeof resp.next_after_seq === 'number') nextAfterSeq = resp.next_after_seq;
			if (resp.has_more) await fetchPage(false);
			if (initial) {
				loading = false;
				const reduced =
					browser && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
				if (reduced || beats.length === 0) {
					timeline.seek(scale.duration);
				} else {
					timeline.play();
				}
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			loading = false;
		}
	}

	onMount(() => {
		void fetchPage(true);
		if (isActive) {
			pollHandle = setInterval(() => {
				if (document.visibilityState === 'visible') void fetchPage(false);
			}, LIVE_POLL_MS);
		}
	});

	onDestroy(() => {
		if (pollHandle) clearInterval(pollHandle);
		timeline.destroy();
	});
</script>

<div data-testid="replay-panel">
	{#if loading}
		<div class="flex justify-center p-8">
			<div class="animate-spin w-6 h-6 border-2 border-primary-500 border-t-transparent rounded-full" />
		</div>
	{:else if error}
		<aside class="alert variant-soft-error"><p>{error}</p></aside>
	{:else if beats.length === 0}
		<aside class="alert variant-soft" data-testid="replay-empty">
			<div>
				<p>{m.replay_no_events()}</p>
				<p class="text-xs opacity-60">{m.replay_unrecorded_hint()}</p>
			</div>
		</aside>
	{:else}
		<PipelineMap nodes={scene.nodes} edges={scene.edges} />
		<div class="mt-2">
			<Transport
				{timeline}
				beatTimes={scale.playTimes}
				realSpanMs={scale.realSpan}
				live={isActive}
			/>
		</div>
		<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
			<NarrationRail {beats} visibleCount={currentIndex + 1} />
			{#if scene.verdict}
				<VerdictPanel verdict={scene.verdict} />
			{/if}
		</div>
	{/if}
</div>
