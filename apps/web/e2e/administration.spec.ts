import { expect, test } from '@playwright/test'

import {
  ADMIN,
  PASSWORD,
  STAMP,
  enrolTotp,
  expectNoSeriousA11yViolations,
  logIn,
} from './support'

/**
 * Administration end to end (CRM01, CRM06): an invitation is the only way in,
 * so it is exercised through two separate browsers; rules start disabled and
 * are reviewed before they act; the calendar is configurable.
 */
test.describe.configure({ mode: 'serial' })

test.skip(!PASSWORD, 'Set E2E_PASSWORD and seed the e2e workspace first.')

test('invite, join, rules and calendar', async ({ page, browser }) => {
  const invitee = `new${STAMP}@scalevexo.test`
  let joinLink = ''

  await test.step('an administrator invites a colleague and gets a one-time link', async () => {
    await logIn(page, ADMIN)
    await enrolTotp(page)
    await page.goto('/settings')
    await page.getByLabel('Email').fill(invitee)
    await page.getByRole('button', { name: 'Create invitation' }).click()
    await expect(page.getByText('It is shown once and cannot be recovered')).toBeVisible()
    joinLink = (await page.locator('code').first().textContent()) ?? ''
    expect(joinLink).toContain('/join?token=')
    await expectNoSeriousA11yViolations(page)
  })

  await test.step('the colleague joins in their own browser and lands on Today', async () => {
    const theirs = await browser.newContext()
    const them = await theirs.newPage()
    await them.goto(joinLink)
    await expect(them.getByText(invitee)).toBeVisible()
    await them.getByLabel('Your name').fill('New Colleague')
    await them.getByLabel('Choose a password').fill(PASSWORD)
    await them.getByLabel('Confirm the password').fill(PASSWORD)
    await them.getByRole('button', { name: 'Join and sign in' }).click()
    await them.waitForURL('**/today')
    await expect(them.getByRole('heading', { name: 'Today', level: 1 })).toBeVisible()

    // The link is single-use.
    const again = await (await browser.newContext()).newPage()
    await again.goto(joinLink)
    await expect(again.getByRole('alert')).toContainText('not valid')
    await theirs.close()
  })

  await test.step('rules install disabled, and are switched on deliberately', async () => {
    await page.goto('/settings')
    const install = page.getByRole('button', { name: 'Install the standard rules' })
    const rule = page.locator('li', { hasText: 'A07 Critical ticket raised' })
    // Either the rules are already installed or the install button offers them.
    await expect(rule.or(install).first()).toBeVisible()
    if (await install.isVisible()) await install.click()
    await expect(rule).toBeVisible()
    if (await rule.getByRole('button', { name: 'Turn on' }).isVisible()) {
      await rule.getByRole('button', { name: 'Turn on' }).click()
    }
    await expect(rule.getByText(/^On · v/)).toBeVisible()
    await rule.getByRole('button', { name: 'Pause' }).click()
    await expect(rule.getByText(/^Paused · v/)).toBeVisible()
    await rule.getByRole('button', { name: 'Resume' }).click()
    await expect(rule.getByText(/^On · v/)).toBeVisible()
  })

  await test.step('the working calendar saves as a new version', async () => {
    const calendar = page.locator('section', {
      has: page.getByRole('heading', { name: 'Working calendar' }),
    })
    await calendar.getByLabel('Holidays').fill('2026-12-25')
    await calendar.getByRole('button', { name: 'Save calendar' }).click()
    await expect(calendar.getByText(/Saved as calendar version \d+/)).toBeVisible()
  })

  await test.step('the team page lists the new colleague', async () => {
    await page.goto('/team')
    await expect(page.getByText(invitee)).toBeVisible()
    await expectNoSeriousA11yViolations(page)
  })
})
