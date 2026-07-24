<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy, onMount } from 'svelte';
	import { api } from '$lib/api/client';
	import { localizedGoto } from '$lib/i18n';
	import { m } from '$lib/paraglide/messages';
	import FleetMap from './FleetMap.svelte';
	import { buildFleetSchedule, LAPSE_MS, type FleetScheduleEntry } from './fleetSchedule';
	import { createTimeline } from './timeline';
	import type { FleetDay } from './types';

	const REFRESH_MS = 60_000;

	let day: FleetDay | null = null;
	let schedule: FleetScheduleEntry[] = [];
	let loading = true;
	let error: string | null = null;
	let refreshHandle: ReturnType<typeof setInterval> | null = null;

	const timeline = createTimeline(LAPSE_MS);

	async function load(initial: boolean) {
		try {
			const tz = browser ? Intl.DateTimeFormat().resolvedOptions().timeZone : 'UTC';
			day = await api.analytics.getFleetDay({ tz });
			schedule = buildFleetSchedule(day);
			if (initial) {
				loading = false;
				const reduced =
					browser && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
				if (reduced) timeline.seek(LAPSE_MS);
				else timeline.play();
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			loading = false;
		}
	}

	onMount(() => {
		void load(true);
		refreshHandle = setInterval(() => {
			if (document.visibilityState === 'visible') void load(false);
		}, REFRESH_MS);
	});

	onDestroy(() => {
		if (refreshHandle) clearInterval(refreshHandle);
		timeline.destroy();
	});

	function drill(e: CustomEvent<{ investigationId: string }>) {
		void localizedGoto(`/investigations/${e.detail.investigationId}?view=replay`);
	}

	const clock = (t: number): string => {
		const mins = (t / LAPSE_MS) * 1440;
		return `${String(Math.floor(mins / 60)).padStart(2, '0')}:${String(Math.floor(mins % 60)).padStart(2, '0')}`;
	};

	$: sampleN = day && day.sample_rate > 0 ? Math.max(1, Math.round(1 / day.sample_rate)) : 1;
	$: closedTotal = day
		? day.closed_ingest_memoized + day.closed_ingest_rules + day.closed_operational + day.closed_reasoning
		: 0;
</script>

<section class="mb-8" data-testid="fleet-panel">
	<h2 class="h3 mb-3">{m.fleet_title()}</h2>
	{#if loading}
		<div class="flex justify-center p-8">
			<div class="animate-spin w-6 h-6 border-2 border-primary-500 border-t-transparent rounded-full" />
		</div>
	{:else if error}
		<aside class="alert variant-soft-error"><p>{error}</p></aside>
	{:else if day}
		<div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
			<div class="xl:col-span-2">
				<div class="card p-3">
					<FleetMap {day} {schedule} t={$timeline.t} on:drill={drill} />
					<div class="flex items-center gap-2 mt-2">
						<button
							type="button"
							class="btn btn-sm variant-soft-primary font-mono"
							on:click={() => timeline.toggle()}
							aria-label={$timeline.playing ? m.replay_pause() : m.replay_play()}
							data-testid="fleet-play"
						>
							{$timeline.playing ? '❚❚' : '▶'}
						</button>
						<button
							type="button"
							class="btn btn-sm variant-soft"
							on:click={() => timeline.restart()}
							aria-label={m.replay_restart()}
						>
							↺
						</button>
						<input
							type="range"
							class="flex-1 min-w-[8rem] accent-primary-500"
							min="0"
							max={LAPSE_MS}
							step="50"
							value={$timeline.t}
							on:input={(e) => {
								timeline.pause();
								timeline.seek(Number(e.currentTarget.value));
							}}
							aria-label={m.replay_scrub()}
						/>
						<span class="text-xs font-mono opacity-70 tabular-nums">{clock($timeline.t)} / 24h</span>
					</div>
					{#if sampleN > 1}
						<p class="text-[0.68rem] font-mono opacity-50 mt-1">
							{m.fleet_sample({ n: sampleN })}
						</p>
					{/if}
					<p class="text-[0.68rem] font-mono opacity-50">{m.fleet_drill_hint()}</p>
				</div>
			</div>

			<div class="space-y-4">
				<div class="card p-4">
					<div class="grid grid-cols-2 gap-4" data-testid="fleet-stats">
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_ingested()}</span>
							<span class="text-xl font-mono tabular-nums">{day.ingested.toLocaleString()}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_closed()}</span>
							<span class="text-xl font-mono tabular-nums text-success-500">{closedTotal.toLocaleString()}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_human()}</span>
							<span class="text-xl font-mono tabular-nums text-warning-500">{day.escalated.toLocaleString()}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_vetoes()}</span>
							<span class="text-xl font-mono tabular-nums">{day.guard_vetoes}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_open()}</span>
							<span class="text-xl font-mono tabular-nums">{day.still_open.toLocaleString()}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_spend()}</span>
							<span class="text-xl font-mono tabular-nums">${day.dollars_used.toFixed(0)}</span>
						</div>
					</div>
				</div>

				<div class="card p-4">
					<h3 class="h5 mb-2">{m.fleet_vetoes_title()}</h3>
					{#if day.recent_vetoes.length === 0}
						<p class="text-sm opacity-60">{m.fleet_none_yet()}</p>
					{:else}
						<div class="space-y-2">
							{#each day.recent_vetoes as veto (veto.investigation_id + veto.at)}
								<button
									type="button"
									class="block w-full text-left text-xs font-mono border-b border-surface-500/20 pb-1 hover:opacity-80"
									on:click={() => void localizedGoto(`/investigations/${veto.investigation_id}?view=replay`)}
								>
									<span class="text-warning-500">{veto.stage ?? 'guard'}</span>
									<span class="text-error-500 block">{veto.fired.join(', ')}</span>
								</button>
							{/each}
						</div>
					{/if}
				</div>
			</div>
		</div>
	{/if}
</section>
