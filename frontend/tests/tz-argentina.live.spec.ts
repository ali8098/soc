/**
 * Live verification of the legacy-tz-alias fix on demo.soctalk.ai (fleet-day).
 *
 * A Buenos Aires browser reports the legacy IANA alias `America/Buenos_Aires`
 * (Chromium's ICU maps every Argentina zone to that display name). Pre-fix the
 * fleet-day endpoint 400'd "unknown timezone" (Python ZoneInfo) or 500'd in the
 * histogram (Postgres AT TIME ZONE), and FleetPanel blanked the whole panel.
 * This drives the DEPLOYED demo with that exact tz and asserts the panel loads
 * and every fleet-day call is 200.
 *
 * ops@ is an mssp_admin, so fleet-day needs a pinned tenant (the screenshot's
 * state). Run:
 *   DEMO_PW=... pnpm exec playwright test --config playwright.tz.config.ts
 */
import { test, expect, type Page } from '@playwright/test';

const ORIGIN = 'https://demo.soctalk.ai';
const EMAIL = 'ops@demo.soctalk.ai';
const PASSWORD = process.env.DEMO_PW ?? '';

// The whole point: emulate an Argentina browser.
test.use({ timezoneId: 'America/Buenos_Aires' });

async function login(page: Page) {
	await page.goto('/login');
	await page.locator('input[type=email]').fill(EMAIL);
	await page.locator('input[type=password]').fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 20000 });
}

test('fleet-day resolves the legacy Buenos Aires tz; panel loads, no 400/500', async ({
	page
}) => {
	test.skip(!PASSWORD, 'set DEMO_PW to run against the live demo');

	// 1. Prove the browser really reports the legacy alias (the bug trigger).
	await page.goto('/login');
	const tz = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
	expect(tz).toBe('America/Buenos_Aires');

	// 2. Record every fleet-day response and the tz it was called with.
	const fleetDay: { status: number; tz: string | null }[] = [];
	page.on('response', (r) => {
		const u = r.url();
		if (u.includes('/api/analytics/fleet-day')) {
			fleetDay.push({ status: r.status(), tz: new URL(u).searchParams.get('tz') });
		}
	});

	await login(page);
	// ops@ logs in with a tenant already pinned (the screenshot's state), so
	// fleet-day has tenant scope without an explicit assume-tenant.

	// 3. Dashboard (live fleet head) — panel must render, not the error aside.
	await page.goto('/');
	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 20000 });
	await expect(page.getByText(/unknown timezone/i)).toHaveCount(0);

	// 4. Replay surface (fleet-day feeds the histogram/replay).
	await page.goto('/analytics');
	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 20000 });
	await expect(page.getByText(/unknown timezone/i)).toHaveCount(0);
	await page.waitForTimeout(1500);

	// 5. The definitive proof: the browser sent the legacy alias, and every
	//    fleet-day call resolved it (200) — both the Python day window and the
	//    Python-bucketed histogram ran without erroring.
	console.log('fleet-day calls:', JSON.stringify(fleetDay));
	expect(fleetDay.length).toBeGreaterThan(0);
	expect(fleetDay.some((r) => r.tz === 'America/Buenos_Aires')).toBeTruthy();
	for (const r of fleetDay) expect(r.status).toBe(200);
});

test('actually plays a time-lapse replay of the last active day', async ({ page }) => {
	test.skip(!PASSWORD, 'set DEMO_PW to run against the live demo');

	await login(page);
	await page.goto('/analytics'); // replay surface, requests the latest-active fallback

	await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 20000 });
	await expect(page.getByText(/unknown timezone/i)).toHaveCount(0);

	// The demo's TODAY is empty, so replay substitutes the most recent active
	// day — surfaced as the fallback badge + a past date label. That is "the
	// last active day".
	await expect(page.getByTestId('fleet-fallback-badge')).toBeVisible({ timeout: 20000 });
	const dayLabel = (await page.getByTestId('fleet-date-label').innerText()).trim();
	console.log('replaying last active day:', dayLabel);

	// The transport is present and idle; the film is parked at the full day.
	const play = page.getByTestId('fleet-play');
	await expect(play).toBeVisible();
	await expect(play).toContainText('▶');
	const scrub = page.getByRole('slider');
	const vStart = Number(await scrub.inputValue());

	// Drive the time-lapse.
	await play.click();
	await expect(play).toContainText('❚❚'); // now playing (pause glyph shown)

	// restart() seeks to 0 then advances the playhead; sample twice and assert
	// it genuinely progresses (the animation is running, not frozen).
	await page.waitForTimeout(1200);
	const v1 = Number(await scrub.inputValue());
	await page.waitForTimeout(2500);
	const v2 = Number(await scrub.inputValue());
	console.log('playhead ms:', { vStart, v1, v2 });

	expect(v1).toBeLessThan(vStart); // restarted from the head
	expect(v2).toBeGreaterThan(v1); // the time-lapse advanced
});
