// Fleet-day dots → time-lapse schedule (issue #72). Every dot is a REAL
// sampled alert from the aggregate endpoint; arrival times are exact.
// Flight time along the map is stretched for legibility and disclosed in
// the caption — at 24h→60s a real triage would be an invisible blip.

import type { FleetArrival, FleetDay, FleetDot } from './types';

export const LAPSE_MS = 60_000; // 24 h → 60 s

export type FleetRoute = 'fast' | 'reason' | 'human' | 'unknown';

export interface FleetScheduleEntry {
	dot: FleetDot;
	route: FleetRoute;
	/** Playback spawn time (ms). */
	t: number;
	/** Playback flight duration (ms). */
	flight: number;
	/** Landing time (ms), clamped into the lapse for day schedules so a
	 * dot arriving near midnight still lands before the film ends. */
	land: number;
	/** Vertical jitter so streams braid instead of beading. */
	jit: number;
}

const FLIGHT: Record<FleetRoute, number> = {
	fast: 1100,
	reason: 2400,
	human: 2800,
	unknown: 700
};

export function routeFor(dot: FleetDot): FleetRoute {
	if (dot.outcome === 'human') return 'human';
	if (dot.path === 'reasoning') return 'reason';
	if (dot.path) return 'fast'; // ingest_memoized | ingest_rules | operational
	return 'unknown';
}

/** Deterministic small jitter from the alert id (no Math.random — replays
 * of the same day must be identical). */
function jitterFor(id: string): number {
	let h = 0;
	for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
	return ((h % 11) - 5) * 1.1;
}

/** Live arrivals render as brief intake pulses on the server clock;
 * entry.t is EPOCH ms (the live map's t is the server clock). */
export const ARRIVAL_FLIGHT_MS = 2500;

export function buildArrivalEntries(arrivals: FleetArrival[]): FleetScheduleEntry[] {
	return arrivals.map((a) => {
		const t = Date.parse(a.first_event_at);
		return {
			dot: {
				alert_id: a.alert_id,
				investigation_id: a.investigation_id,
				first_event_at: a.first_event_at,
				closed_at: null,
				path: null,
				outcome: 'open' as const,
				veto: false
			},
			route: 'unknown' as const,
			t,
			flight: ARRIVAL_FLIGHT_MS,
			land: t + ARRIVAL_FLIGHT_MS,
			jit: jitterFor(a.alert_id)
		};
	});
}

/** Per-class dot totals for a schedule — invariant per schedule build, so
 * compute once (not inside the per-frame scan). */
export interface FleetScheduleTotals {
	dots: number;
	closed: number;
	human: number;
	vetoes: number;
}

export function scheduleTotals(schedule: FleetScheduleEntry[]): FleetScheduleTotals {
	let closed = 0;
	let human = 0;
	let vetoes = 0;
	for (const e of schedule) {
		if (e.route === 'fast' || e.route === 'reason') closed++;
		else if (e.route === 'human') human++;
		if (e.dot.veto) vetoes++;
	}
	return { dots: schedule.length, closed, human, vetoes };
}

/** Landed-so-far tallies at a lapse playhead. A dot counts when its
 * animation lands in a column (its clamped `land` time), so counters tick
 * in step with what the film shows; project onto the exact aggregates via
 * progressiveExact so the numbers end on the true totals. */
export interface FleetLapseCounts {
	arrived: number;
	closed: number;
	human: number;
	vetoes: number;
}

export function countsAt(schedule: FleetScheduleEntry[], t: number): FleetLapseCounts {
	let arrived = 0;
	let closed = 0;
	let human = 0;
	let vetoes = 0;
	for (const e of schedule) {
		if (e.t > t) continue;
		arrived++;
		if (e.land <= t) {
			if (e.route === 'fast' || e.route === 'reason') closed++;
			else if (e.route === 'human') human++;
			if (e.dot.veto) vetoes++;
		}
	}
	return { arrived, closed, human, vetoes };
}

/** Project a landed-dot fraction onto the exact aggregate so progressive
 * counters end exactly on the true total (dots may be a sample, and event
 * counts can exceed dot counts — attachments, reopens). When the sample
 * has NO dots of a class but the aggregate is nonzero, be explicit rather
 * than dishonest: show 0 during the film and snap to the exact total only
 * at the end of the lapse. */
export function progressiveExact(
	landed: number,
	totalDots: number,
	exact: number,
	atEnd: boolean
): number {
	if (totalDots > 0) return Math.round(exact * (landed / totalDots));
	return atEnd ? exact : 0;
}

export function buildFleetSchedule(day: FleetDay): FleetScheduleEntry[] {
	const start = Date.parse(day.window_start);
	const end = Date.parse(day.window_end);
	const span = Math.max(1, end - start);
	return day.dots
		.map((dot) => {
			const route = routeFor(dot);
			const t = ((Date.parse(dot.first_event_at) - start) / span) * LAPSE_MS;
			return {
				dot,
				route,
				t,
				flight: FLIGHT[route],
				// Clamp: a midnight-adjacent dot must still land inside the
				// film, or end-of-lapse counters undershoot the day totals.
				land: Math.min(LAPSE_MS, t + FLIGHT[route]),
				jit: jitterFor(dot.alert_id)
			};
		})
		.sort((a, b) => a.t - b.t);
}
