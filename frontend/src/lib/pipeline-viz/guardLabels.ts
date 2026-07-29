// Analyst-friendly labels for guard stages and fired-rule codes (#72).
// The wire vocabulary (verdict_guard, server_floor, worker_floor,
// operational; authorization_contradicted_close, active_incident,
// ioc_over_close, correlation) is an implementation detail — surfaces
// show these labels and keep the raw code in a tooltip for audit.
// Unknown codes fall back to a de-snake-cased rendering, never blank.
import { m } from '$lib/paraglide/messages';

const STAGE: Record<string, () => string> = {
	verdict_guard: () => m.guard_stage_verdict(),
	server_floor: () => m.guard_stage_floor(),
	worker_floor: () => m.guard_stage_floor(),
	operational: () => m.guard_stage_operational()
};

// Two producers share this vocabulary with slightly different spellings:
// the verdict guard (guard.py GUARDRAIL_*) and the close floor (floor.py
// VETO_*). Map both, plus legacy fixture aliases.
const REASON: Record<string, () => string> = {
	authorization_contradicted_close: () => m.guard_reason_authz(),
	authorization_contradicted: () => m.guard_reason_authz(),
	authz_contradicted: () => m.guard_reason_authz(),
	active_incident: () => m.guard_reason_incident(),
	ioc_over_close: () => m.guard_reason_ioc(),
	ioc_present: () => m.guard_reason_ioc(),
	ioc_unverified: () => m.guard_reason_ioc_unverified(),
	correlation: () => m.guard_reason_correlation(),
	sensitive_asset_close_signoff: () => m.guard_reason_signoff(),
	auto_close_killed: () => m.guard_reason_kill_switch(),
	close_volume_cap: () => m.guard_reason_volume_cap()
};

const deSnake = (code: string) => code.replace(/_/g, ' ');

export function guardStageLabel(stage: string | null | undefined): string {
	if (!stage) return m.guard_stage_generic();
	return STAGE[stage]?.() ?? deSnake(stage);
}

export function guardReasonLabel(code: string): string {
	return REASON[code]?.() ?? deSnake(code);
}

export function guardReasonsLabel(codes: string[]): string {
	return codes.map(guardReasonLabel).join(' · ');
}
