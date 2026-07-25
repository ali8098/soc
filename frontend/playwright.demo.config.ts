// Config for running against the LIVE demo box — no local dev server,
// no mocks. Serves demo-record.tmp.spec.ts (walkthrough recording) and
// demo-correctness.live.spec.ts (read-only UI correctness suite).
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	testDir: './tests',
	testMatch: /demo-(record\.tmp|correctness\.live)\.spec\.ts/,
	fullyParallel: false,
	reporter: 'list',
	use: {
		baseURL: 'https://demo.soctalk.ai',
		trace: 'off',
		screenshot: 'only-on-failure',
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
