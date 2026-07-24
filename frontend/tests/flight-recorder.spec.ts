import { test, expect } from '@playwright/test';
import { TENANT_ID, mockAuthMe } from './helpers';

const INV_ID = '550e8400-e29b-41d4-a716-446655440072';

const investigation = {
	id: INV_ID,
	title: 'RDP failures then success',
	status: 'escalated',
	phase: 'verdict',
	created_at: new Date(Date.now() - 3600_000).toISOString(),
	updated_at: new Date().toISOString(),
	closed_at: null,
	alert_count: 1,
	observable_count: 3,
	malicious_count: 0,
	suspicious_count: 1,
	clean_count: 2,
	max_severity: 'high',
	verdict_decision: 'escalate',
	thehive_case_id: null,
	tenant_id: TENANT_ID,
	time_to_triage_seconds: 12,
	time_to_verdict_seconds: 81,
	verdict_confidence: 0.78,
	verdict_reasoning: null,
	threat_actor: null,
	tags: [],
	tokens_used: 41700,
	tokens_budget: 200000,
	disposition: 'escalate'
};

// A guard-veto journey: the model proposes close, the floor flips it.
const T0 = Date.parse('2026-07-24T10:00:00Z');
const at = (s: number) => new Date(T0 + s * 1000).toISOString();
const replayEvents = [
	{ seq: 1, event_type: 'alert_ingested', timestamp: at(0), data: { rule_id: '60122' } },
	{ seq: 2, event_type: 'policy_resolved', timestamp: at(1), data: { triage_policy: null, deterministic_disposition: null } },
	{ seq: 3, event_type: 'supervisor_decision', timestamp: at(2), data: { next_action: 'INVESTIGATE', action_reasoning: 'pull context', tp_confidence: 0.4, iteration: 1 } },
	{ seq: 4, event_type: 'worker_started', timestamp: at(3), data: { worker: 'wazuh', action: 'INVESTIGATE' } },
	{ seq: 5, event_type: 'worker_result', timestamp: at(12), data: { worker: 'wazuh', ok: true, summary: '11 failures then success' } },
	{ seq: 6, event_type: 'verdict_rendered', timestamp: at(60), data: { decision: 'close', confidence: 0.78, key_evidence: ['pattern matches vendor maintenance'], gaps_in_evidence: ['revoked grant unweighed'], recommendation: 'close as routine' } },
	{ seq: 7, event_type: 'guard_evaluated', timestamp: at(79), data: { stage: 'verdict_guard', decision_in: 'close', decision_out: 'escalate', effect: 'override', fired: ['authz_contradicted'] } },
	{ seq: 8, event_type: 'human_review_requested', timestamp: at(81), data: { reason: 'guard override', verdict_decision: 'escalate' } }
].map((e, i) => ({ ...e, id: `evt-${i + 1}`, run_id: 'run-1', visibility: 'mssp_only' }));

function eventsResponse(events: typeof replayEvents) {
	return {
		investigation_id: INV_ID,
		events,
		total: events.length,
		server_now: new Date().toISOString(),
		next_after_seq: events.length > 0 ? events[events.length - 1].seq : 0,
		has_more: false
	};
}

test.describe('Investigation replay (flight recorder #72)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuthMe(page, { current_tenant: TENANT_ID, current_tenant_slug: 'acme' });
		await page.route(`**/api/investigations/${INV_ID}`, (route) =>
			route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(investigation) })
		);
	});

	test('plays a guard-veto journey and shows the flip', async ({ page }) => {
		await page.route(`**/api/investigations/${INV_ID}/events*`, (route) => {
			const url = new URL(route.request().url());
			const events = url.searchParams.get('order') === 'asc' ? replayEvents : [...replayEvents].reverse();
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(eventsResponse(events))
			});
		});

		await page.goto(`/investigations/${INV_ID}?view=replay`);
		await expect(page.locator('div.animate-spin')).not.toBeVisible({ timeout: 10000 });
		await expect(page.getByTestId('pipeline-map')).toBeVisible();

		// Seek determinism: jump the scrubber to the end and assert the final
		// scene — the same t must always produce the same frame.
		const scrubber = page.getByTestId('replay-transport').locator('input[type="range"]');
		await scrubber.evaluate((el) => {
			const input = el as HTMLInputElement;
			input.value = input.max;
			input.dispatchEvent(new Event('input', { bubbles: true }));
		});

		await expect(page.getByTestId('verdict-decision')).toContainText('escalate');
		await expect(page.locator('[data-node="guard"]')).toHaveAttribute('data-state', 'veto');
		await expect(page.locator('[data-node="human"]')).toHaveAttribute('data-state', 'warn');
		await expect(page.getByTestId('narration-rail')).toContainText('authz_contradicted');
	});

	test('shows the honest empty state for pre-instrumentation investigations', async ({ page }) => {
		await page.route(`**/api/investigations/${INV_ID}/events*`, (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(eventsResponse([]))
			})
		);
		await page.goto(`/investigations/${INV_ID}?view=replay`);
		await expect(page.locator('div.animate-spin')).not.toBeVisible({ timeout: 10000 });
		await expect(page.getByTestId('replay-empty')).toBeVisible();
	});
});

test.describe('Fleet flight recorder on analytics (#72)', () => {
	test.beforeEach(async ({ page }) => {
		await mockAuthMe(page, { current_tenant: TENANT_ID, current_tenant_slug: 'acme' });
		await page.route('**/api/analytics/summary*', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					period_start: '2026-07-17T00:00:00Z',
					period_end: '2026-07-24T00:00:00Z',
					executive_kpis: {
						auto_close_rate: 0.93,
						escalation_rate: 0.06,
						human_override_rate: 0,
						mean_time_to_decision_seconds: 62,
						total_investigations: 1247,
						auto_closed_count: 1163,
						escalated_count: 84,
						human_reviewed_count: 84,
						avg_ai_confidence: 0.86,
						high_confidence_rate: 0.7
					},
					ai_behavior: {
						confidence_distribution: [],
						decision_trends: [],
						escalation_breakdown: [],
						avg_confidence_by_decision: {}
					},
					human_review: {
						total_reviews: 84,
						pending: 2,
						approved: 60,
						rejected: 10,
						info_requested: 8,
						expired: 4,
						approval_rate: 0.7,
						override_rate: 0.1,
						ai_agreed_count: 70,
						ai_overridden_count: 10,
						avg_review_time_seconds: 300
					},
					outcomes: {
						total_closed: 1163,
						closed_as_false_positive: 1100,
						closed_as_true_positive: 40,
						closed_as_suspicious: 23,
						avg_resolution_time_seconds: 90,
						p: null
					}
				})
			})
		);
		await page.route('**/api/analytics/fleet-day*', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					date: '2026-07-24',
					tz: 'UTC',
					server_now: new Date().toISOString(),
					window_start: '2026-07-24T00:00:00+00:00',
					window_end: '2026-07-25T00:00:00+00:00',
					ingested: 1247,
					closed_ingest_memoized: 300,
					closed_ingest_rules: 428,
					closed_operational: 16,
					closed_reasoning: 419,
					escalated: 84,
					guard_vetoes: 14,
					still_open: 0,
					ingest_histogram: Array(24).fill(52),
					dollars_used: 198,
					tokens_used: 20_000_000,
					sample_rate: 0.4,
					dots: [
						{
							alert_id: 'a-1',
							investigation_id: 'i-1',
							first_event_at: '2026-07-24T09:00:00+00:00',
							closed_at: '2026-07-24T09:01:00+00:00',
							path: 'ingest_rules',
							outcome: 'closed',
							veto: false
						},
						{
							alert_id: 'a-2',
							investigation_id: 'i-2',
							first_event_at: '2026-07-24T10:00:00+00:00',
							closed_at: null,
							path: null,
							outcome: 'human',
							veto: true
						}
					],
					recent_vetoes: [
						{
							investigation_id: 'i-2',
							at: '2026-07-24T10:02:00+00:00',
							stage: 'verdict_guard',
							fired: ['authz_contradicted']
						}
					]
				})
			})
		);
	});

	test('renders exact counters, the glyph map, and the veto ticker', async ({ page }) => {
		await page.goto('/analytics');
		await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 10000 });
		await expect(page.getByTestId('fleet-map')).toBeVisible();
		const stats = page.getByTestId('fleet-stats');
		await expect(stats).toContainText('1,247');
		await expect(stats).toContainText('84');
		await expect(stats).toContainText('14');
		await expect(page.getByTestId('fleet-panel')).toContainText('authz_contradicted');
		// Sampling disclosure must be on-canvas when sample_rate < 1.
		await expect(page.getByTestId('fleet-panel')).toContainText('1 in 3');
	});
});
