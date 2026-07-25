/**
 * Flight recorder live e2e (#72) — NO route mocks.
 *
 * Drives the real V1 backend end to end: real login (internal auth), real
 * tenant pin, and replay/fleet surfaces fed by genuine pipeline events
 * produced by an actual runs-worker triage (real LLM runs).
 *
 * Prerequisites (the verify-skill recipe):
 * - Postgres on 55432, migrations at head
 * - uvicorn app_v1 on 127.0.0.1:8000 with SOCTALK_PUBLIC_ORIGIN=http://localhost:5173
 * - seeded mssp admin e2e-admin@acme.example / e2e-admin-pw-12345, tenant 'acme'
 * - at least one completed triage run (replay beats recorded)
 *
 * Run: pnpm exec playwright test tests/flight-e2e.live.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';

const ORIGIN = 'http://localhost:5173';
const EMAIL = 'e2e-admin@acme.example';
const PASSWORD = 'e2e-admin-pw-12345';

test.describe.configure({ mode: 'serial' });

async function login(page: Page) {
	await page.goto('/login');
	await page.locator('input[type=email]').fill(EMAIL);
	await page.locator('input[type=password]').fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByText(EMAIL)).toBeVisible({ timeout: 15000 });
}

async function pinTenant(page: Page) {
	const resp = await page.request.post('/api/auth/assume-tenant', {
		headers: { Origin: ORIGIN, 'Content-Type': 'application/json' },
		data: { slug: 'acme' }
	});
	expect(resp.ok()).toBeTruthy();
}

test('live: home dashboard runs the fleet live head on real data', async ({ page }) => {
	// Skip the catch-up intro so the head is immediately assertable.
	await page.addInitScript(() => {
		sessionStorage.setItem(
			`soctalk-fleet-catchup-${new Date().toISOString().slice(0, 10)}`,
			'1'
		);
	});
	await login(page);
	await pinTenant(page);
	await page.goto('/');

	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 15000 });
	await expect(page.getByTestId('fleet-live-chip')).toBeVisible({ timeout: 15000 });
	await expect(page.getByTestId('fleet-live-clock')).not.toHaveText('—', { timeout: 10000 });

	// Cross-check the rendered stats against the live API through the same
	// session — the UI must agree with the wire.
	const live = await (
		await page.request.get('/api/analytics/fleet-live', { headers: { Origin: ORIGIN } })
	).json();
	expect(live.ingested).toBeGreaterThanOrEqual(2);
	await expect(page.getByTestId('fleet-stats')).toContainText(String(live.ingested));
	await expect(page.getByTestId('fleet-in-flight')).toHaveText(String(live.in_flight));
});

test('live: analytics keeps the day recap behind an explicit control', async ({ page }) => {
	await login(page);
	await pinTenant(page);
	await page.goto('/analytics');

	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 15000 });
	// Replay mode by default on analytics: play button present, not autoplaying.
	await expect(page.getByTestId('fleet-play')).toBeVisible();
	await expect(page.getByTestId('fleet-live-chip')).toHaveCount(0);
	// Exact counters from the real fleet-day aggregate.
	const day = await (
		await page.request.get('/api/analytics/fleet-day', { headers: { Origin: ORIGIN } })
	).json();
	expect(day.ingested).toBeGreaterThanOrEqual(2);
	await expect(page.getByTestId('fleet-stats')).toContainText(String(day.ingested));
});

test('live: a real triage run replays on the investigation detail page', async ({ page }) => {
	await login(page);
	await pinTenant(page);

	// Find the brute-force investigation through the real list API.
	const list = await (
		await page.request.get('/api/investigations?page_size=50', { headers: { Origin: ORIGIN } })
	).json();
	expect(list.items.length).toBeGreaterThanOrEqual(2);
	const inv =
		list.items.find((i: { title?: string | null }) => i.title?.includes('brute force')) ??
		list.items[0];

	await page.goto(`/investigations/${inv.id}?view=replay`);
	await expect(page.getByTestId('pipeline-map')).toBeVisible({ timeout: 20000 });

	// Deterministic seek to the end of the film.
	const scrubber = page.getByTestId('replay-transport').locator('input[type="range"]');
	await scrubber.evaluate((el) => {
		const input = el as HTMLInputElement;
		input.value = input.max;
		input.dispatchEvent(new Event('input', { bubbles: true }));
	});

	// Real beats from the real run: policy gate, supervisor routing, a
	// verdict, and a guard ruling must all have been recorded and rendered.
	const rail = page.getByTestId('narration-rail');
	await expect(rail).toContainText('Policy gate');
	await expect(rail).toContainText('Supervisor routed');
	await expect(rail).toContainText('Verdict:');
	await expect(rail).toContainText(/Guard check passed|Guard override|Guard interrupt/);
	await expect(page.getByTestId('verdict-panel')).toBeVisible();
	await expect(page.getByTestId('verdict-decision')).toContainText(
		/close|escalate|needs_more_info/
	);

	// Regression: the legacy Timeline view still renders the same feed.
	await page.getByTestId('view-timeline').click();
	await expect(page.getByTestId('pipeline-map')).toHaveCount(0);
	await expect(page.locator('.card').filter({ hasText: 'Event Timeline' })).toBeVisible();
});

test('live: cursor feed contract holds against the real API', async ({ page }) => {
	await login(page);
	await pinTenant(page);
	const list = await (
		await page.request.get('/api/investigations?page_size=50', { headers: { Origin: ORIGIN } })
	).json();
	const inv = list.items[0];

	const cursor = await (
		await page.request.get(
			`/api/investigations/${inv.id}/events?after_seq=0&order=asc&limit=500`,
			{ headers: { Origin: ORIGIN } }
		)
	).json();
	expect(cursor.server_now).toBeTruthy();
	expect(cursor.events.length).toBeGreaterThan(0);
	// Ascending, gapless-cursor semantics.
	const seqs = cursor.events.map((e: { seq: number }) => e.seq);
	expect([...seqs].sort((a, b) => a - b)).toEqual(seqs);
	expect(cursor.next_after_seq).toBe(seqs[seqs.length - 1]);

	// Legacy shape regression: the no-cursor call still returns newest-first.
	const legacy = await (
		await page.request.get(`/api/investigations/${inv.id}/events?limit=10`, {
			headers: { Origin: ORIGIN }
		})
	).json();
	expect(legacy.events.length).toBeGreaterThan(0);
	const legacySeqs = legacy.events.map((e: { seq: number }) => e.seq);
	expect([...legacySeqs].sort((a, b) => b - a)).toEqual(legacySeqs);
});
