// Throwaway recording (#72): the seeded flight recorder on the LIVE demo
// box. Login → home (live head) → analytics (time-lapse of the seeded
// day) → a guard-veto replay film → timeline. Video on.
import { test, expect } from '@playwright/test';

const ORIGIN = 'https://demo.soctalk.ai';
const EMAIL = 'recorder@demo.soctalk.ai';
const PASSWORD = process.env.RECORDER_PW ?? '';

// Viewer vantage: the seeded day straddles local midnight once the
// recording session runs late — pick a tz where the full seeded day is
// still "today" (arrivals AND closes in one lapse window).
test.use({
	viewport: { width: 1440, height: 960 },
	// 720p recording buffer: full-res muxing starved under load and hung
	// context teardown past the test timeout (video lost entirely).
	video: { mode: 'on', size: { width: 1280, height: 853 } },
	timezoneId: process.env.RECORD_TZ || 'America/Los_Angeles'
});

test('demo box: seeded fleet time-lapse and replay', async ({ page }) => {
	test.setTimeout(480_000);

	// 1. Real tenant login on the live box (demo.soctalk.ai is the
	// tenant-scoped UI — no MSSP pin involved)
	await page.goto('/login');
	await page.waitForTimeout(1000);
	await page.locator('input[type=email]').fill(EMAIL);
	await page.locator('input[type=password]').fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 20000 });

	// 2. Home: catch-up cam → live head over the seeded day
	await page.goto('/');
	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 20000 });
	await page.getByTestId('fleet-panel').scrollIntoViewIfNeeded();
	await page.waitForTimeout(10000); // catch-up + live breathing

	// 3. Analytics: replay the full seeded day deliberately
	await page.goto('/analytics');
	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 20000 });
	await page.getByTestId('fleet-panel').scrollIntoViewIfNeeded();
	await page.waitForTimeout(1500);
	await page.getByTestId('fleet-play').click();
	await page.waitForTimeout(14000); // 327 dots crossing the day

	// 4. Pick a human-lane (guard-veto) investigation via the API
	const list = await (
		await page.request.get('/api/investigations?page_size=100', {
			headers: { Origin: ORIGIN }
		})
	).json();
	const items: { id: string; status?: string; created_at?: string }[] = list.items ?? [];
	const fresh = items
		.filter((i) => i.status === 'active')
		.sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''));
	const inv = fresh[0] ?? items[0];
	expect(inv).toBeTruthy();

	// 5. The film: autoplays on the Replay view
	await page.goto(`/investigations/${inv.id}?view=replay`);
	await expect(page.getByTestId('pipeline-map')).toBeVisible({ timeout: 25000 });
	await page.waitForTimeout(9000); // full film + hold on the guard beat
	await page.getByTestId('narration-rail').scrollIntoViewIfNeeded();
	await page.waitForTimeout(3000);

	// 6. Same feed as classic timeline (best-effort: the recording's value
	// is the segments above — never let this coda eat the whole budget)
	try {
		await page.getByTestId('view-timeline').click({ timeout: 10000 });
		await page.waitForTimeout(3000);
	} catch {
		// leave the film on the replay view
	}
});
