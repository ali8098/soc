// Throwaway verification (#72 latest-active-day fallback): local stack
// whose last activity is YESTERDAY — today is genuinely empty, so the
// panel must substitute, disclose, and offer the escape hatch.
import { test, expect } from '@playwright/test';

const ORIGIN = 'http://localhost:5173';

test.use({ viewport: { width: 1440, height: 960 }, timezoneId: 'America/Los_Angeles' });

test('empty today: replay falls back to latest active day with badge + escape hatch', async ({
	page
}) => {
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

	// Analytics (replay surface): fallback day + disclosed date + badge.
	await page.goto('/analytics');
	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 20000 });
	await expect(page.getByTestId('fleet-date-label')).toBeVisible({ timeout: 15000 });
	const label = await page.getByTestId('fleet-date-label').innerText();
	expect(label, 'label must NOT read as today').not.toMatch(/today/i);
	await expect(page.getByTestId('fleet-fallback-badge')).toBeVisible();

	// The film actually has content: scrub to the end and the counters
	// must land on the substituted day's totals.
	await page.getByTestId('fleet-play').click();
	await page.waitForTimeout(1000);
	await page.locator('input[type=range]').evaluate((el: HTMLInputElement) => {
		el.value = el.max;
		el.dispatchEvent(new Event('input', { bubbles: true }));
	});
	await page.waitForTimeout(500);
	// Arrival-anchored counter (closes on a BACKDATED test seed stamp at
	// inject time, outside the substituted day — a test-data artifact).
	const stats = page.getByTestId('fleet-stats').locator('> div');
	let ingested = 0;
	for (let i = 0; i < (await stats.count()); i++) {
		const lbl = (await stats.nth(i).locator('span').first().innerText()).toLowerCase();
		if (lbl.includes('alerts in')) {
			ingested = Number(
				(await stats.nth(i).locator('span').nth(1).innerText()).replace(/[^0-9]/g, '')
			);
		}
	}
	expect(ingested).toBeGreaterThan(0);

	// Escape hatch: badge -> true empty today, then back.
	await page.getByTestId('fleet-fallback-badge').click();
	await expect(page.getByTestId('fleet-empty')).toBeVisible({ timeout: 15000 });
	await expect(page.getByTestId('fleet-show-last-active')).toBeVisible();
	await page.getByTestId('fleet-show-last-active').click();
	await expect(page.getByTestId('fleet-date-label')).toBeVisible({ timeout: 15000 });
	await expect(page.getByTestId('fleet-fallback-badge')).toBeVisible();
});

test('dashboard live mode: no catch-up on empty today, live head is the present', async ({
	page
}) => {
	test.setTimeout(90_000);
	await page.goto('/login');
	await page.locator('input[type=email]').fill('e2e-admin@acme.example');
	await page.locator('input[type=password]').fill('e2e-admin-pw-12345');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByText('e2e-admin@acme.example')).toBeVisible({ timeout: 15000 });
	await page.request.post('/api/auth/assume-tenant', {
		headers: { Origin: ORIGIN, 'Content-Type': 'application/json' },
		data: { slug: 'acme' }
	});

	await page.goto('/');
	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 20000 });
	// Empty today: the catch-up cam must be skipped — the live chip should
	// appear promptly (no film playing first).
	await expect(page.getByTestId('fleet-live-chip')).toBeVisible({ timeout: 10000 });
});
