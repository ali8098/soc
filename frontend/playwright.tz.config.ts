// One-off config for the Argentina-tz live check against the deployed demo.
// No local dev server, no mocks. Run:
//   RECORDER_PW=... pnpm exec playwright test --config playwright.tz.config.ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	testDir: './tests',
	testMatch: /tz-argentina\.live\.spec\.ts/,
	fullyParallel: false,
	reporter: 'list',
	use: {
		baseURL: 'https://demo.soctalk.ai',
		trace: 'off',
		screenshot: 'only-on-failure'
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
});
