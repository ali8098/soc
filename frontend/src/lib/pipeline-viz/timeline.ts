// Playback clock as a Svelte store (issue #72).
//
// The CLOCK is performance.now(); requestAnimationFrame only schedules
// paints (a backgrounded tab throttles rAF, and on return the elapsed time
// is recomputed from the monotonic clock — no drift). Rendering must stay a
// pure function of the store's `t`.

import { writable, type Readable } from 'svelte/store';

export interface TimelineState {
	t: number;
	duration: number;
	playing: boolean;
	ended: boolean;
}

export interface Timeline extends Readable<TimelineState> {
	play: () => void;
	pause: () => void;
	toggle: () => void;
	seek: (t: number) => void;
	setDuration: (d: number) => void;
	/** Playback rate multiplier (catch-up cam uses >1). */
	setRate: (rate: number) => void;
	restart: () => void;
	destroy: () => void;
}

export function createTimeline(duration = 0): Timeline {
	const state: TimelineState = { t: 0, duration, playing: false, ended: false };
	const { subscribe, set } = writable<TimelineState>({ ...state });
	let raf: number | null = null;
	let lastNow: number | null = null;
	let rate = 1;

	const emit = () => set({ ...state });

	const frame = () => {
		if (!state.playing) return;
		const now = performance.now();
		if (lastNow !== null) {
			state.t = Math.min(state.t + (now - lastNow) * rate, state.duration);
		}
		lastNow = now;
		if (state.t >= state.duration) {
			state.playing = false;
			state.ended = true;
			lastNow = null;
			emit();
			return;
		}
		emit();
		raf = requestAnimationFrame(frame);
	};

	const play = () => {
		if (state.playing) return;
		if (state.t >= state.duration) state.t = 0;
		state.playing = true;
		state.ended = false;
		lastNow = null;
		emit();
		if (typeof requestAnimationFrame !== 'undefined') raf = requestAnimationFrame(frame);
	};

	const pause = () => {
		state.playing = false;
		lastNow = null;
		if (raf !== null && typeof cancelAnimationFrame !== 'undefined') cancelAnimationFrame(raf);
		raf = null;
		emit();
	};

	return {
		subscribe,
		play,
		pause,
		toggle: () => (state.playing ? pause() : play()),
		seek: (t: number) => {
			state.t = Math.max(0, Math.min(t, state.duration));
			state.ended = state.t >= state.duration;
			lastNow = null;
			emit();
		},
		setDuration: (d: number) => {
			state.duration = Math.max(0, d);
			state.t = Math.min(state.t, state.duration);
			emit();
		},
		setRate: (r: number) => {
			rate = Math.max(0.1, r);
		},
		restart: () => {
			state.t = 0;
			state.ended = false;
			emit();
			play();
		},
		destroy: () => {
			pause();
		}
	};
}
