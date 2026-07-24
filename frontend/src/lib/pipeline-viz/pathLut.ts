// Mount-time SVG path sampling (issue #72). getPointAtLength is the
// expensive SVG call — sample each named path ONCE into a Float32Array and
// interpolate per frame. Browser-only: callers must be inside onMount.

export interface PathLut {
	/** Interleaved x,y samples. */
	pts: Float32Array;
	samples: number;
}

export function buildLut(path: SVGPathElement, samples = 128): PathLut {
	const len = path.getTotalLength();
	const pts = new Float32Array((samples + 1) * 2);
	for (let i = 0; i <= samples; i++) {
		const p = path.getPointAtLength((i / samples) * len);
		pts[i * 2] = p.x;
		pts[i * 2 + 1] = p.y;
	}
	return { pts, samples };
}

export function lutPoint(lut: PathLut, frac: number): { x: number; y: number } {
	const f = Math.max(0, Math.min(1, frac)) * lut.samples;
	const i = Math.floor(f);
	const r = f - i;
	const j = Math.min(i + 1, lut.samples);
	return {
		x: lut.pts[i * 2] * (1 - r) + lut.pts[j * 2] * r,
		y: lut.pts[i * 2 + 1] * (1 - r) + lut.pts[j * 2 + 1] * r
	};
}
