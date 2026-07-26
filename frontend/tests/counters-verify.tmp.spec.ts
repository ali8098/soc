// Throwaway verification (#72 counter fix): during the day replay the
// stat rail and column labels must ACCUMULATE with the playhead and end
// on the exact day totals.
import { test, expect } from '@playwright/test';

const ORIGIN = 'http://localhost:5173';

// Pin the browser tz to UTC: the seeded closes sit in the current UTC
// day regardless of when this runs locally.
test.use({ viewport: { width: 1440, height: 960 }, timezoneId: 'UTC' });

test('replay counters accumulate with the playhead', async ({ page }) => {
	test.setTimeout(120_000);

	await page.goto('/login');
	await page.locator('input[type=email]').fill('e2e-admin@acme.example');
	await page.locator('input[type=password]').fill('e2e-admin-pw-12345');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByText('e2e-admin@acme.example')).toBeVisible({ timeout: 15000 });
	const pin = await page.request.post('/api/auth/assume-tenant', {
		headers: { Origin: ORIGIN, 'Content-Type': 'application/json' },
		data: { slug: 'acme' }
	});
	expect(pin.ok()).toBeTruthy();

	await page.goto('/analytics');
	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 15000 });
	const day = await (
		await page.request.get('/api/analytics/fleet-day', { headers: { Origin: ORIGIN } })
	).json();
	const exactClosed =
		day.closed_ingest_memoized +
		day.closed_ingest_rules +
		day.closed_operational +
		day.closed_reasoning;

	const stats = page.getByTestId('fleet-stats');
	const closedTile = stats.locator('span.text-success-500');
	const humanTile = stats.locator('span.text-warning-500');
	const readNum = async (el: typeof closedTile) =>
		Number((await el.innerText()).replace(/[^0-9]/g, ''));

	// Restart the replay from 0: counters must drop to ~0, then grow.
	await page.getByTestId('fleet-play').click();
	await page.waitForTimeout(400);
	const early = await readNum(closedTile);
	await page.waitForTimeout(2500);
	const mid = await readNum(closedTile);
	await page.waitForTimeout(3000);
	const midHuman = await readNum(humanTile);
	// Let the lapse run out (60s total; scrub to the end instead of waiting).
	await page
		.locator('input[type=range]')
		.evaluate((el: HTMLInputElement) => {
			el.value = el.max;
			el.dispatchEvent(new Event('input', { bubbles: true }));
		});
	await page.waitForTimeout(600);
	const final = await readNum(closedTile);
	const finalHuman = await readNum(humanTile);

	console.log({ early, mid, midHuman, final, finalHuman, exactClosed, dayEscalated: day.escalated });
	expect(early).toBeLessThan(mid);
	expect(mid).toBeLessThanOrEqual(final);
	expect(final).toBe(exactClosed);
	expect(finalHuman).toBe(day.escalated);
	await page.screenshot({ path: 'test-results/counters-final.png' });
});
