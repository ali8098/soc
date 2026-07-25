<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy, onMount } from 'svelte';
	import { api } from '$lib/api/client';
	import SegmentedTabs from '$lib/components/SegmentedTabs.svelte';
	import { localizedGoto } from '$lib/i18n';
	import { formatNumber, formatTime } from '$lib/i18n/format';
	import { m } from '$lib/paraglide/messages';
	import { formatRelativeAge } from '$lib/utils/formatters';
	import FleetMap from './FleetMap.svelte';
	import {
		buildArrivalEntries,
		buildFleetSchedule,
		countsAt,
		LAPSE_MS,
		progressiveExact,
		scheduleTotals,
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

	// The lapse clock is a synthetic 24h position, not a wall time — the
	// live clock and ages go through the locale-aware shared formatters.
	const lapseClock = (t: number): string => {
		const mins = (t / LAPSE_MS) * 1440;
		return `${String(Math.floor(mins / 60)).padStart(2, '0')}:${String(Math.floor(mins % 60)).padStart(2, '0')}`;
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
	$: showLiveHead = mode === 'live' && livePhase === 'head';
	// Replay and the catch-up cam track the playhead: counters accumulate
	// as dots land (projected onto the exact aggregates so they end on
	// the true totals). Only the live head binds to the live snapshot.
	// Totals are invariant per schedule — keep them out of the per-frame
	// path (Codex P2).
	$: totals = scheduleTotals(schedule);
	$: lapse = day && !showLiveHead ? countsAt(schedule, $timeline.t) : null;
	$: lapseAtEnd = $timeline.t >= LAPSE_MS - 1;
	$: statIngested =
		showLiveHead && live
			? live.ingested
			: lapse && day
				? progressiveExact(lapse.arrived, totals.dots, day.ingested, lapseAtEnd)
				: (day?.ingested ?? 0);
	$: statClosed =
		showLiveHead && live
			? liveClosed
			: lapse
				? progressiveExact(lapse.closed, totals.closed, dayClosed, lapseAtEnd)
				: dayClosed;
	$: statEscalated =
		showLiveHead && live
			? live.escalated
			: lapse && day
				? progressiveExact(lapse.human, totals.human, day.escalated, lapseAtEnd)
				: (day?.escalated ?? 0);
	$: statVetoes =
		showLiveHead && live
			? live.guard_vetoes
			: lapse && day
				? progressiveExact(lapse.vetoes, totals.vetoes, day.guard_vetoes, lapseAtEnd)
				: (day?.guard_vetoes ?? 0);
	// The veto rail reveals each ruling when its DOT lands — the same
	// clock statVetoes ticks on, so a row never appears while the stat
	// still reads 0 (Codex P2). Rows whose investigation was sampled out
	// of the dot set (sample_rate < 1 only) fall back to their real
	// guard-decision time; the stat's projection covers them in aggregate.
	const vetoLapseT = (d: FleetDay, at: string): number => {
		const start = Date.parse(d.window_start);
		const span = Math.max(1, Date.parse(d.window_end) - start);
		return ((Date.parse(at) - start) / span) * LAPSE_MS;
	};
	// Earliest land per investigation: attached alerts give one
	// investigation several dots, and the row should appear with the
	// first of them (min, not last-wins).
	$: vetoLandByInv = (() => {
		const map = new Map<string, number>();
		for (const e of schedule) {
			if (!e.dot.veto || !e.dot.investigation_id) continue;
			const prev = map.get(e.dot.investigation_id);
			if (prev === undefined || e.land < prev) map.set(e.dot.investigation_id, e.land);
		}
		return map;
	})();
	$: visibleVetoes = (() => {
		const d = day;
		if (!d) return [];
		if (!lapse) return d.recent_vetoes;
		return d.recent_vetoes.filter(
			(v) => (vetoLandByInv.get(v.investigation_id) ?? vetoLapseT(d, v.at)) <= $timeline.t
		);
	})();
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
		<div class="ml-auto">
			<SegmentedTabs
				value={mode}
				options={[
					{ id: 'live', label: m.fleet_mode_live(), testid: 'fleet-mode-live' },
					{ id: 'replay', label: m.fleet_mode_replay(), testid: 'fleet-mode-replay' }
				]}
				on:change={(e) => switchMode(e.detail.id === 'live' ? 'live' : 'replay')}
			/>
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
								{$clock ? formatTime($clock) : '—'}
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
								<span class="text-xl font-mono tabular-nums" data-testid="fleet-last-alert">{formatRelativeAge(live.last_alert_at, $clock)}</span>
							</div>
						{/if}
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_ingested()}</span>
							<span class="text-xl font-mono tabular-nums">{formatNumber(statIngested)}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_closed()}</span>
							<span class="text-xl font-mono tabular-nums text-success-500">{formatNumber(statClosed)}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_human()}</span>
							<span class="text-xl font-mono tabular-nums text-warning-500">{formatNumber(statEscalated)}</span>
						</div>
						<div>
							<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_vetoes()}</span>
							<span class="text-xl font-mono tabular-nums">{statVetoes}</span>
						</div>
						{#if mode === 'replay'}
							<div>
								<span class="text-[0.65rem] uppercase tracking-wider opacity-50 block">{m.fleet_open()}</span>
								<span class="text-xl font-mono tabular-nums">{formatNumber(day.still_open)}</span>
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
					{#if visibleVetoes.length === 0}
						<p class="text-sm opacity-60">{m.fleet_none_yet()}</p>
					{:else}
						<div class="space-y-2">
							{#each visibleVetoes as veto (veto.investigation_id + veto.at)}
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
