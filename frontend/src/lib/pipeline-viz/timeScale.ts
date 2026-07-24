// Deterministic piecewise time compression (issue #72). Long quiet gaps
// compress hard, decision moments hold; two viewers of the same
// investigation always see the same film. Pure and unit-testable.

import type { Beat } from './types';

/** Per-gap playback duration in ms: clamp real gaps into a watchable band. */
export function playGap(realGapMs: number): number {
	if (realGapMs <= 0) return 0;
	const MIN = 350;
	const MAX = 1400;
	if (realGapMs <= MIN) return realGapMs;
	// Logarithmic squash above the minimum: 1s → ~700ms, 60s → ~1.2s.
	const squashed = MIN + 260 * Math.log10(1 + realGapMs / 1000);
	return Math.min(MAX, squashed);
}

export interface TimeScale {
	/** Playback timestamp (ms) for each beat, same order as input. */
	playTimes: number[];
	/** Total playback duration in ms (includes a closing hold). */
	duration: number;
	/** Total real span in ms. */
	realSpan: number;
}

const CLOSING_HOLD_MS = 600;

export function buildTimeScale(beats: Beat[]): TimeScale {
	if (beats.length === 0) return { playTimes: [], duration: 0, realSpan: 0 };
	const playTimes: number[] = [0];
	for (let i = 1; i < beats.length; i++) {
		playTimes.push(playTimes[i - 1] + playGap(beats[i].tReal - beats[i - 1].tReal));
	}
	return {
		playTimes,
		duration: playTimes[playTimes.length - 1] + CLOSING_HOLD_MS,
		realSpan: beats[beats.length - 1].tReal
	};
}

/** Index of the last beat with playTime <= t, or -1. */
export function beatIndexAt(scale: TimeScale, t: number): number {
	let lo = 0;
	let hi = scale.playTimes.length - 1;
	let ans = -1;
	while (lo <= hi) {
		const mid = (lo + hi) >> 1;
		if (scale.playTimes[mid] <= t) {
			ans = mid;
			lo = mid + 1;
		} else {
			hi = mid - 1;
		}
	}
	return ans;
}
