<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy, onMount } from 'svelte';
	import { api } from '$lib/api/client';
	import { localizedGoto } from '$lib/i18n';
	import { m } from '$lib/paraglide/messages';
	import FleetMap from './FleetMap.svelte';
	import {
		buildArrivalEntries,
		buildFleetSchedule,
		LAPSE_MS,
		type FleetScheduleEntry
	} from './fleetSchedule';
	import { createServerClock } from './serverClock';
	import { createTimeline } from './timeline';
	import type { FleetDay, FleetLive } from './types';

	/** 'live' = present tense on the server clock (tenant home).
	 *  'replay' = the day as an explicit, non-autoplaying recap (analytics). */
	export let defaultMode: 'live' | 'replay' = 'replay';

	const DAY_REFRESH_MS = 60_000;
	const LIVE_POLL_MS = 7_000;
	const CATCHUP_PLAY_MS = 4_500;

	let mode: 'live' | 'replay' = defaultMode;
	/** Within live mode: the once-per-session catch-up intro, then the head. */
	let livePhase: 'catchup' | 'head' = 'head';

	let day: FleetDay | null = null;
	let live: FleetLive | null = null;
	let schedule: FleetScheduleEntry[] = [];
	let arrivalEntries: FleetScheduleEntry[] = [];
	let loading = true;
	let error: string | null = null;
	let dayHandle: ReturnType<typeof setInterval> | null = null;
	let liveHandle: ReturnType<typeof setInterval> | null = null;

	const timeline = createTimeline(LAPSE_MS);
	const clock = createServerClock();

	const tzName = () =>
		browser ? Intl.DateTimeFormat().resolvedOptions().timeZone : 'UTC';
	const reducedMotion = () =>
		browser && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

	function dayFrac(d: FleetDay, serverNowIso: string): number {
		const start = Date.parse(d.window_start);
		const end = Date.parse(d.window_end);
		return Math.max(0, Math.min(1, (Date.parse(serverNowIso) - start) / (end - start)));
	}

	async function loadDay(initial: boolean) {
		try {
			day = await api.analytics.getFleetDay({ tz: tzName() });
			schedule = buildFleetSchedule(day);
			if (initial) {
				loading = false;
				if (mode === 'replay') {
					// Recap surface: land on "the day so far", play only on demand.
					timeline.setRate(1);
					timeline.seek(dayFrac(day, day.server_now) * LAPSE_MS);
				}
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			loading = false;
		}
	}

	async function pollLive() {
		try {
			live = await api.analytics.getFleetLive({ tz: tzName() });
			clock.sync(live.server_now);
			arrivalEntries = buildArrivalEntries(live.recent_arrivals);
		} catch (e) {
			if (!day) {
				error = e instanceof Error ? e.message : String(e);
				loading = false;
			}
		}
	}

	function catchupKey(): string {
		return `soctalk-fleet-catchup-${new Date().toISOString().slice(0, 10)}`;
	}

	function maybeStartCatchup() {
		// Once per session per day, never under reduced motion (Codex/NN|g:
		// a ritual replayed on every visit becomes an ignored roadblock).
		if (!browser || !day || reducedMotion()) return;
		if (sessionStorage.getItem(catchupKey())) return;
		sessionStorage.setItem(catchupKey(), '1');
		const upto = dayFrac(day, live?.server_now ?? day.server_now) * LAPSE_MS;
		if (upto < 500) return;
		livePhase = 'catchup';
		timeline.seek(0);
		timeline.setDuration(upto);
		timeline.setRate(Math.max(1, upto / CATCHUP_PLAY_MS));
		timeline.play();
	}

	$: if (mode === 'live' && livePhase === 'catchup' && $timeline.ended) {
		livePhase = 'head';
	}

	function switchMode(next: 'live' | 'replay') {
		if (mode === next) return;
		mode = next;
		if (next === 'live') {
			livePhase = 'head';
			clock.start();
			if (!liveHandle) {
				void pollLive();
				liveHandle = setInterval(() => {
					if (document.visibilityState === 'visible') void pollLive();
				}, LIVE_POLL_MS);
			}
		} else {
			timeline.pause();
			timeline.setRate(1);
			timeline.setDuration(LAPSE_MS);
			if (day) timeline.seek(dayFrac(day, live?.server_now ?? day.server_now) * LAPSE_MS);
		}
	}

	onMount(() => {
		void loadDay(true).then(() => {
			if (mode === 'live') {
				clock.start();
				void pollLive().then(() => maybeStartCatchup());
				liveHandle = setInterval(() => {
					if (document.visibilityState === 'visible') void pollLive();
				}, LIVE_POLL_MS);
			}
		});
		dayHandle = setInterval(() => {
			if (document.visibilityState === 'visible') void loadDay(false);
		}, DAY_REFRESH_MS);
	});

	onDestroy(() => {
		if (dayHandle) clearInterval(dayHandle);
		if (liveHandle) clearInterval(liveHandle);
		timeline.destroy();
		clock.destroy();
	});

	function drill(e: CustomEvent<{ investigationId: string }>) {
		void localizedGoto(`/investigations/${e.detail.investigationId}?view=replay`);
	}

	const lapseClock = (t: number): string => {
		const mins = (t / LAPSE_MS) * 1440;
		return `${String(Math.floor(mins / 60)).padStart(2, '0')}:${String(Math.floor(mins % 60)).padStart(2, '0')}`;
	};
	const liveClockLabel = (epochMs: number): string => {
		if (!epochMs) return '—';
		const d = new Date(epochMs);
		return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
	};
	const fmtAgo = (iso: string | null, nowMs: number): string => {
		if (!iso || !nowMs) return '—';
		const s = Math.max(0, Math.floor((nowMs - Date.parse(iso)) / 1000));
		return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
	};

	$: sampleN = day && day.sample_rate > 0 ? Math.max(1, Math.round(1 / day.sample_rate)) : 1;
	$: liveClosed = live
		? live.closed_ingest_memoized +
			live.closed_ingest_rules +
			live.closed_operational +
			live.closed_reasoning
		: 0;
	$: dayClosed = day
		? day.closed_ingest_memoized + day.closed_ingest_rules + day.closed_operational + day.closed_reasoning
		: 0;
	$: statIngested = mode === 'live' && live ? live.ingested : (day?.ingested ?? 0);
	$: statClosed = mode === 'live' && live ? liveClosed : dayClosed;
	$: statEscalated = mode === 'live' && live ? live.escalated : (day?.escalated ?? 0);
	$: statVetoes = mode === 'live' && live ? live.guard_vetoes : (day?.guard_vetoes ?? 0);
	$: showLiveHead = mode === 'live' && livePhase === 'head';
	$: unknownOpen = live?.open_by_stage?.unknown ?? 0;
</script>

<section class="mb-8" data-testid="fleet-panel">
	<div class="flex items-center gap-3 mb-3">
		<h2 class="h3">{m.fleet_title()}</h2>
		{#if showLiveHead}
			<span class="badge variant-soft-error font-mono text-[0.65rem] live-chip" data-testid="fleet-live-chip">
				<span class="live-dot" />
				{m.replay_live()}
			</span>
		{/if}
		<div class="btn-group variant-soft ml-auto" role="tablist">
			<button
				type="button"
				class="btn btn-sm {mode === 'live' ? 'variant-filled-primary' : ''}"
				role="tab"
				aria-selected={mode === 'live'}
				on:click={() => switchMode('live')}
				data-testid="fleet-mode-live"
			>
				{m.fleet_mode_live()}
			</button>
			<button
				type="button"
				class="btn btn-sm {mode === 'replay' ? 'variant-filled-primary' : ''}"
				role="tab"
				aria-selected={mode === 'replay'}
				on:click={() => switchMode('replay')}
				data-testid="fleet-mode-replay"
			>
				{m.fleet_mode_replay()}
			</button>
		</div>
	</div>
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
					{#if showLiveHead}
						<FleetMap
							{day}
							schedule={arrivalEntries}
							t={$clock}
							mode="live"
							openByStage={live?.open_by_stage ?? {}}
							liveCounts={live
								? {
										ingested: live.ingested,
										closed: liveClosed,
										reasoning: live.closed_reasoning,
										escalated: live.escalated
									}
								: null}
							on:drill={drill}
						/>
						<div class="flex items-center gap-3 mt-2">
							<span class="text-xs font-mono opacity-70 tabular-nums" data-testid="fleet-live-clock">
								{liveClockLabel($clock)}
							</span>
							<p class="text-[0.68rem] font-mono opacity-50">{m.fleet_live_caption()}</p>
						</div>
					{:else}
						<FleetMap {day} {schedule} t={$timeline.t} on:drill={drill} />
						<div class="flex items-center gap-2 mt-2">
							<button
								type="button"
								class="btn btn-sm variant-soft-primary font-mono"
								on:click={() => (mode === 'live' ? undefined : $timeline.playing ? timeline.pause() : timeline.restart())}
								aria-label={$timeline.playing ? m.replay_pause() : m.fleet_replay_day()}
								data-testid="fleet-play"
								disabled={mode === 'live'}
							>
								{$timeline.playing ? '❚❚' : '▶'}
							</button>
							{#if mode === 'replay'}
								<span class="text-[0.7rem] opacity-60">{m.fleet_replay_hint()}</span>
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
							{/if}
							<span class="text-xs font-mono opacity-70 tabular-nums">{lapseClock($timeline.t)} / 24h</span>
						</div>
					{/if}
					{#if !showLiveHead && sampleN > 1}
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
						{#if showLiveHead && live}
							<div>
								<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_in_flight()}</span>
								<span class="text-xl font-mono tabular-nums" data-testid="fleet-in-flight">{live.in_flight}</span>
								{#if unknownOpen > 0}
									<span class="text-[0.6rem] opacity-40 block">{m.fleet_stage_unknown({ n: unknownOpen })}</span>
								{/if}
							</div>
							<div>
								<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_last_alert()}</span>
								<span class="text-xl font-mono tabular-nums" data-testid="fleet-last-alert">{fmtAgo(live.last_alert_at, $clock)}</span>
							</div>
						{/if}
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_ingested()}</span>
							<span class="text-xl font-mono tabular-nums">{statIngested.toLocaleString()}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_closed()}</span>
							<span class="text-xl font-mono tabular-nums text-success-500">{statClosed.toLocaleString()}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_human()}</span>
							<span class="text-xl font-mono tabular-nums text-warning-500">{statEscalated.toLocaleString()}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_vetoes()}</span>
							<span class="text-xl font-mono tabular-nums">{statVetoes}</span>
						</div>
						{#if mode === 'replay'}
							<div>
								<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_open()}</span>
								<span class="text-xl font-mono tabular-nums">{day.still_open.toLocaleString()}</span>
							</div>
							<div>
								<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_spend()}</span>
								<span class="text-xl font-mono tabular-nums">${day.dollars_used.toFixed(0)}</span>
							</div>
						{/if}
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

<style>
	.live-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}
	.live-dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: rgb(var(--color-error-500));
		animation: blink 1.4s steps(2) infinite;
	}
	@keyframes blink {
		50% {
			opacity: 0.25;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.live-dot {
			animation: none;
		}
	}
</style>
