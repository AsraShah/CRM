/// <reference lib="dom" />
// DOM types for code that runs inside page.evaluate().
import { createHmac } from 'node:crypto'

import AxeBuilder from '@axe-core/playwright'
import { expect, type Page } from '@playwright/test'

export const PASSWORD = process.env.E2E_PASSWORD ?? ''
export const OWNER = 'e2e-owner@scalevexo.test'
/** A separate administrator, so the two journeys each enrol their own TOTP. */
export const ADMIN = 'e2e-admin@scalevexo.test'
export const DELIVERY = 'e2e-delivery@scalevexo.test'
export const REP = 'e2e-rep@scalevexo.test'

/** A unique suffix so repeated runs against one database do not collide. */
export const STAMP = Date.now().toString().slice(-6)

function base32Decode(input: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const char of input.replace(/=+$/, '').toUpperCase()) {
    bits += alphabet.indexOf(char).toString(2).padStart(5, '0')
  }
  const bytes: number[] = []
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2))
  return Buffer.from(bytes)
}

/** A current TOTP code, computed as an authenticator app would (RFC 6238). */
export function totp(secret: string): string {
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30)))
  const hmac = createHmac('sha1', base32Decode(secret)).update(counter).digest()
  const offset = hmac[hmac.length - 1]! & 0xf
  return ((hmac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).toString().padStart(6, '0')
}

export async function logIn(page: Page, email: string): Promise<void> {
  await page.goto('/accounts/login/')
  await page.fill('input[name="login"]', email)
  await page.fill('input[name="password"]', PASSWORD)
  await page.locator('form button[type="submit"]').last().click()
  await page.waitForURL(/\/(today|accounts\/2fa)/)
}

/** Enrol TOTP through allauth's real page, which unlocks MFA-gated capabilities. */
export async function enrolTotp(page: Page): Promise<void> {
  await page.goto('/accounts/2fa/totp/activate/')
  const secret = await page.inputValue('#authenticator_secret')
  await page.fill('input[name="code"]', totp(secret))
  await page.locator('form button[type="submit"]').last().click()
  await page.waitForLoadState('networkidle')
}

/**
 * Automated accessibility check (WCAG 2.2 AA target, section 7.3). Automated
 * scans are necessary but not sufficient; the manual keyboard and screen-reader
 * checks are recorded separately.
 */
export async function expectNoSeriousA11yViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(
    serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`),
  ).toEqual([])
}

/** Fails if the page can be scrolled sideways (360px target, section 7.3). */
export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  expect(overflow).toBeLessThanOrEqual(1)
}
