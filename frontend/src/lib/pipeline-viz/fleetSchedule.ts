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
	return arrivals.map((a) => ({
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
		t: Date.parse(a.first_event_at),
		flight: ARRIVAL_FLIGHT_MS,
		jit: jitterFor(a.alert_id)
	}));
}

export function buildFleetSchedule(day: FleetDay): FleetScheduleEntry[] {
	const start = Date.parse(day.window_start);
	const end = Date.parse(day.window_end);
	const span = Math.max(1, end - start);
	return day.dots
		.map((dot) => {
			const route = routeFor(dot);
			return {
				dot,
				route,
				t: ((Date.parse(dot.first_event_at) - start) / span) * LAPSE_MS,
				flight: FLIGHT[route],
				jit: jitterFor(dot.alert_id)
			};
		})
		.sort((a, b) => a.t - b.t);
}
