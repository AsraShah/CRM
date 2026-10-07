import { expect, test, type Page } from '@playwright/test'

import {
  DELIVERY,
  OWNER,
  PASSWORD,
  STAMP,
  enrolTotp,
  expectNoHorizontalScroll,
  expectNoSeriousA11yViolations,
  logIn,
} from './support'

/**
 * The primary business journey, end to end, through the real interface:
 * lead -> qualified -> deal -> proposal -> won -> handover -> milestone review
 * -> ticket resolution -> receipt -> reporting (SVX-TECH-001 sections 7, 13).
 *
 * One serial test with named steps: each step depends on the records the last
 * one created, and a failure names the step that broke.
 */
test.describe.configure({ mode: 'serial' })

test.skip(!PASSWORD, 'Set E2E_PASSWORD and seed the e2e workspace first.')

const CONTACT = `Acme ${STAMP}`
const DEAL = `SEO retainer ${STAMP}`

const dialog = (page: Page) => page.getByRole('dialog')

test('lead to resolved ticket', async ({ page }) => {
  await test.step('the owner signs in and enrols a second factor', async () => {
    await logIn(page, OWNER)
    await enrolTotp(page)
    await page.goto('/today')
    await expect(page.getByRole('heading', { name: 'Today', level: 1 })).toBeVisible()
    // MFA unlocked the gated capabilities, so Settings appears.
    await expect(page.getByRole('link', { name: 'Settings' })).toBeVisible()
    await expectNoSeriousA11yViolations(page)
  })

  await test.step('a lead is captured with the minimum fields', async () => {
    await page.goto('/leads')
    await page.getByRole('button', { name: 'New lead' }).click()
    await dialog(page).getByLabel('Name').fill(CONTACT)
    await dialog(page).getByLabel('Email').fill(`buyer${STAMP}@acme.test`)
    await dialog(page).getByRole('button', { name: 'Create lead' }).click()
    await expect(dialog(page)).toBeHidden()
    await expect(page.getByRole('rowheader', { name: CONTACT })).toBeVisible()
    await expectNoSeriousA11yViolations(page)
  })

  await test.step('a call is logged with its follow-up in one step', async () => {
    await page.getByRole('button', { name: `Open ${CONTACT}` }).click()
    await dialog(page).getByLabel('Outcome').fill('Intro call; wants an SEO retainer.')
    await dialog(page).getByLabel('Follow-up', { exact: true }).fill(`Send pricing ${STAMP}`)
    await dialog(page).getByRole('button', { name: 'Record activity' }).click()
    await expect(dialog(page).getByText('Follow-up scheduled on Today.')).toBeVisible()
    // A manual entry is never shown as verified (CRM04).
    await expect(dialog(page).getByText('Self-reported').first()).toBeVisible()
  })

  await test.step('the lead is qualified with need and fit, and a deal opened', async () => {
    await dialog(page).getByRole('button', { name: 'Status' }).click()
    await dialog(page).locator('select').selectOption('qualified')
    await dialog(page).getByLabel('Need').fill('Organic traffic has stalled.')
    await dialog(page).getByLabel('Fit').fill('Matches our six-month retainer.')
    await dialog(page).getByRole('button', { name: 'Change status' }).click()
    await expect(dialog(page)).toBeHidden()

    await page.getByRole('button', { name: `Open ${CONTACT}` }).click()
    await dialog(page).getByRole('button', { name: 'Open a deal' }).click()
    await dialog(page).getByLabel('What is being sold?').fill(DEAL)
    await dialog(page).getByRole('button', { name: 'Open deal' }).click()
    await expect(dialog(page).getByText('The deal is on the pipeline')).toBeVisible()
    await dialog(page).getByRole('button', { name: 'Done' }).click()
  })

  await test.step('the unknown value becomes known by editing the deal', async () => {
    await page.goto('/pipeline')
    const card = page.locator('li', { hasText: DEAL })
    await expect(card.getByText('Not yet known')).toBeVisible()
    await card.getByRole('button', { name: `Edit ${DEAL}` }).click()
    await dialog(page).getByLabel(/^Value/).fill('4500')
    await dialog(page).getByRole('button', { name: 'Save' }).click()
    await expect(dialog(page)).toBeHidden()
    await expect(card.getByText('Not yet known')).toBeHidden()
  })

  await test.step('the deal moves to negotiation without drag and drop', async () => {
    const card = () => page.locator('li', { hasText: DEAL })
    for (const stage of ['qualified', 'proposal', 'negotiation']) {
      await card().getByLabel('Move to stage').selectOption(stage)
      if (stage === 'proposal') await card().getByLabel('Scope reference').fill('Proposal v2')
      await card().getByRole('button', { name: `Move stage for ${DEAL}` }).click()
      await expect(card().getByLabel('Move to stage')).toHaveValue('')
    }
  })

  await test.step('closing as won hands over to delivery', async () => {
    const card = page.locator('li', { hasText: DEAL })
    await card.getByLabel('Move to stage').selectOption('won')
    await card.getByRole('button', { name: `Close as won for ${DEAL}` }).click()
    await dialog(page).getByLabel('What scope did the client accept?').fill('Signed proposal v2.')
    await dialog(page).getByLabel('Commercial decision reference').fill(`PO-${STAMP}`)
    await dialog(page)
      .getByLabel('Delivery owner')
      .selectOption({ label: `Dana Delivery (${DELIVERY})` })
    await dialog(page).getByRole('button', { name: 'Close as won and hand over' }).click()
    // The confirmation must survive the deal leaving the open pipeline.
    await expect(dialog(page).getByText(/is now a client/)).toBeVisible()
    await dialog(page).getByRole('button', { name: 'Done' }).click()
  })

  await test.step('delivery accepts the handover; the customer record shows the sale', async () => {
    await page.goto('/clients')
    await page.getByRole('button', { name: `Accept handover for ${CONTACT}` }).click()
    await page.getByRole('button', { name: 'Confirm acceptance' }).click()
    await page.getByLabel('Awaiting handover only').uncheck()
    await page.getByRole('link', { name: CONTACT }).click()
    await expect(page.getByRole('heading', { name: CONTACT, level: 1 })).toBeVisible()
    await expect(page.getByText('Signed proposal v2.')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'How the sale went' })).toBeVisible()
    await expectNoSeriousA11yViolations(page)
  })

  await test.step('a milestone is started, submitted, sent back, and accepted', async () => {
    await page.goto('/projects')
    const project = page
      .getByRole('listitem')
      .filter({ has: page.getByRole('heading', { level: 2, name: new RegExp(CONTACT) }) })
    const first = project.locator('ol > li').first()
    const name = (await first.locator('[data-milestone-name]').textContent())!.replace(/^\d+\.\s*/, '')

    await project.getByRole('button', { name: `Start work: ${name}` }).click()
    await dialog(page).getByRole('button', { name: 'Start work' }).click()
    await expect(dialog(page)).toBeHidden()

    const submit = async (evidence: string) => {
      await project.getByRole('button', { name: `Submit for review: ${name}` }).click()
      await dialog(page).getByLabel(/Evidence the work is done/).fill(evidence)
      await dialog(page).getByRole('button', { name: 'Submit for review' }).click()
      await expect(dialog(page)).toBeHidden()
    }
    await submit('Kick-off notes, draft.')

    await project.getByRole('button', { name: `Send back: ${name}` }).click()
    await dialog(page).getByLabel(/What must change/).fill('Add the agreed agenda.')
    await dialog(page).getByRole('button', { name: 'Send back' }).click()
    await expect(dialog(page)).toBeHidden()

    await submit('Kick-off notes with agenda.')
    await project.getByRole('button', { name: `Review and accept: ${name}` }).click()
    await expect(dialog(page).getByText('You submitted this work yourself')).toBeVisible()
    await dialog(page).getByRole('button', { name: 'Review and accept' }).click()
    await expect(project.getByRole('button', { name: `Reopen: ${name}` })).toBeVisible()
    await expectNoSeriousA11yViolations(page)
  })

  await test.step('a ticket waits, resolves with a closure test, and reopens to triage', async () => {
    await page.goto('/tickets')
    await page.getByRole('button', { name: 'New ticket' }).click()
    await dialog(page).getByLabel('Title').fill(`Form broken ${STAMP}`)
    await dialog(page).getByLabel(/^Client/).selectOption({ label: CONTACT })
    await dialog(page).getByRole('button', { name: 'Create ticket' }).click()
    await expect(dialog(page)).toBeHidden()

    const go = async (button: string, fill?: () => Promise<void>) => {
      await page.getByRole('button', { name: button, exact: true }).click()
      if (fill) await fill()
      await dialog(page).getByRole('button', { name: button, exact: true }).click()
      await expect(dialog(page)).toBeHidden()
    }
    await go('Triage')
    await go('Start work')
    await go('Wait on the customer', () =>
      dialog(page).getByLabel('What do we need from the customer?').fill('SMTP credentials.'),
    )
    await go('Resume work')
    await go('Resolve', async () => {
      await dialog(page).getByLabel(/^Resolution/).fill('Configured the SMTP relay.')
      await dialog(page).getByLabel(/^Closure test/).fill('Form submitted; email arrived.')
    })
    await expect(page.getByText('Closure test:')).toBeVisible()
    await go('Reopen', () =>
      dialog(page).getByLabel('Why is it being reopened?').fill('It broke again.'),
    )
    await expect(page.getByText(/Reopened 1×/).first()).toBeVisible()
  })

  await test.step('cash is recorded only against evidence, and figures drill down', async () => {
    await page.goto('/reports')
    const receipts = page.locator('section', {
      has: page.getByRole('heading', { name: 'Cash receipts' }),
    })
    await receipts.getByLabel(/^Client/).selectOption({ label: CONTACT })
    await receipts.getByLabel('Amount').fill('1500')
    await receipts.getByLabel(/Evidence reference/).fill(`BANK-${STAMP}`)
    await receipts.getByRole('button', { name: 'Record receipt' }).click()
    await expect(receipts.getByText('Receipt recorded.')).toBeVisible()

    await page.getByRole('button', { name: 'Show the receipts' }).click()
    await expect(dialog(page).getByText(`${CONTACT} · BANK-${STAMP}`)).toBeVisible()
    await dialog(page).getByRole('button', { name: 'Close', exact: true }).click()
    await expectNoSeriousA11yViolations(page)
  })

  await test.step('core screens fit a 360-pixel-wide phone', async () => {
    await page.setViewportSize({ width: 360, height: 800 })
    for (const route of ['/today', '/leads', '/pipeline', '/projects', '/tickets', '/team', '/reports']) {
      await page.goto(route)
      await page.waitForLoadState('networkidle')
      await expectNoHorizontalScroll(page)
    }
  })
})
