import { defineConfig, devices } from '@playwright/test'

/**
 * Browser journeys (SVX-TECH-001 section 13: "Playwright journeys cover import,
 * assignment, follow-up, proposal, win, onboarding, milestone review and
 * ticket resolution").
 *
 * They run against a live API and interface with the e2e workspace seeded:
 *
 *   E2E_PASSWORD=... python manage.py seed_e2e_workspace   (apps/api)
 *   pnpm dev                                                 (apps/web)
 *   E2E_PASSWORD=... pnpm e2e                                (apps/web)
 *
 * Set E2E_CHROME_CHANNEL=chrome to use an installed Chrome instead of the
 * downloaded Chromium.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: {
        ...devices['Desktop Chrome'],
        ...(process.env.E2E_CHROME_CHANNEL ? { channel: process.env.E2E_CHROME_CHANNEL } : {}),
      },
    },
  ],
})
