// Throwaway walkthrough recording (#72): one continuous real-data session.
// Login → home (catch-up cam → live head) → analytics (replay the day) →
// investigations → a genuine triage replay → timeline. Video on.
import { test, expect } from '@playwright/test';

const ORIGIN = 'http://localhost:5173';

test.use({ viewport: { width: 1440, height: 960 }, video: { mode: 'on', size: { width: 1440, height: 960 } } });

test('walkthrough: flight recorder on real data', async ({ page }) => {
	test.setTimeout(180_000);

	// 1. Real login
	await page.goto('/login');
	await page.waitForTimeout(800);
	await page.locator('input[type=email]').fill('e2e-admin@acme.example');
	await page.locator('input[type=password]').fill('e2e-admin-pw-12345');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByText('e2e-admin@acme.example')).toBeVisible({ timeout: 15000 });
	const pin = await page.request.post('/api/auth/assume-tenant', {
		headers: { Origin: ORIGIN, 'Content-Type': 'application/json' },
		data: { slug: 'acme' }
	});
	expect(pin.ok()).toBeTruthy();

	// 2. Home: first visit of the day → catch-up cam, then the live head
	await page.goto('/');
	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 15000 });
	await page.getByTestId('fleet-panel').scrollIntoViewIfNeeded();
	await expect(page.getByTestId('fleet-live-chip')).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(9000); // live head breathing, one poll cycle

	// 3. Analytics: the demoted day recap — press play deliberately
	await page.goto('/analytics');
	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 15000 });
	await page.getByTestId('fleet-panel').scrollIntoViewIfNeeded();
	await page.waitForTimeout(1500);
	await page.getByTestId('fleet-play').click();
	await page.waitForTimeout(9000); // dots crossing the day

	// 4. Investigations → the real brute-force triage
	await page.goto('/investigations');
	await expect(page.getByText('SSH brute force attempt on web-01')).toBeVisible({ timeout: 15000 });
	await page.waitForTimeout(1500);
	const list = await (
		await page.request.get('/api/investigations?page_size=50', { headers: { Origin: ORIGIN } })
	).json();
	const inv =
		list.items.find((i: { title?: string | null }) => i.title?.includes('brute force')) ??
		list.items[0];

	// 5. The film: autoplays on opening the Replay view
	await page.goto(`/investigations/${inv.id}?view=replay`);
	await expect(page.getByTestId('pipeline-map')).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(8000); // full film + hold on the ending
	await page.getByTestId('narration-rail').scrollIntoViewIfNeeded();
	await page.waitForTimeout(2500);

	// 6. Same feed, classic timeline — the regression view
	await page.getByTestId('view-timeline').click();
	await page.waitForTimeout(2500);
});
