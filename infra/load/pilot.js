// RD03 - budget host capacity (SVX-TECH-001 sections 11.2 and 17).
//
// Ten concurrent interactive sessions against the 'load' fixture, mixing the
// ordinary reads and writes of a working day. The pass conditions are the
// specification's targets, encoded as thresholds so the run fails loudly:
//
//   p95 ordinary reads  < 500 ms
//   p95 ordinary writes < 1 s
//   unexpected errors   < 1 %
//
//   k6 run -e BASE_URL=https://pilot.example -e PASSWORD=... infra/load/pilot.js
//   k6 run -e DURATION=30m ...    # the 30-minute measured run
//   k6 run -e DURATION=24h ...    # the soak, with scheduled backups running
//
// Record the raw summary, host metrics (memory, disk, oldest due job), the
// commit and the date in docs/experiments/RD03.md. A run on a developer laptop
// is not RD03 evidence: the experiment is defined on the 2 GiB reference host.

import http from 'k6/http'
import { check, group, sleep } from 'k6'
import { Rate, Trend } from 'k6/metrics'

const BASE = __ENV.BASE_URL || 'http://localhost:5173'
const PASSWORD = __ENV.PASSWORD
const USERS = 7 // load00..load06 are the sales users in the fixture.

const reads = new Trend('ordinary_reads', true)
const writes = new Trend('ordinary_writes', true)
const unexpected = new Rate('unexpected_errors')

export const options = {
  scenarios: {
    working_day: {
      executor: 'constant-vus',
      vus: 10,
      duration: __ENV.DURATION || '5m',
    },
  },
  thresholds: {
    ordinary_reads: ['p(95)<500'],
    ordinary_writes: ['p(95)<1000'],
    unexpected_errors: ['rate<0.01'],
  },
}

function csrf(jar) {
  const cookies = jar.cookiesForURL(BASE)
  return (cookies.csrftoken || [''])[0]
}

function login(index) {
  const jar = http.cookieJar()
  http.get(`${BASE}/accounts/login/`)
  const email = `load${String(index).padStart(2, '0')}@scalevexo.test`
  const response = http.post(
    `${BASE}/accounts/login/`,
    { login: email, password: PASSWORD, csrfmiddlewaretoken: csrf(jar) },
    { headers: { Referer: `${BASE}/accounts/login/` }, redirects: 0 },
  )
  check(response, { 'logged in': (r) => r.status === 302 })
  return jar
}

function read(path) {
  const response = http.get(`${BASE}/api/v1${path}`, { tags: { kind: 'read' } })
  reads.add(response.timings.duration)
  unexpected.add(response.status >= 400)
  return response
}

function write(path, body, jar) {
  const response = http.post(`${BASE}/api/v1${path}`, JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(jar) },
    tags: { kind: 'write' },
  })
  writes.add(response.timings.duration)
  unexpected.add(response.status >= 400)
  return response
}

export default function () {
  if (!PASSWORD) throw new Error('Pass -e PASSWORD=... (the fixture users\' password).')
  const jar = login(__VU % USERS)

  group('morning: today and the pipeline', () => {
    read('/today/')
    read('/leads/?page_size=50')
    read('/opportunities/?page_size=50')
  })

  group('logging a call with its follow-up', () => {
    const deals = read('/opportunities/?page_size=20').json('results') || []
    if (deals.length) {
      const deal = deals[Math.floor(Math.random() * deals.length)]
      const due = new Date(Date.now() + 86_400_000).toISOString()
      write(
        '/activities/',
        {
          contact: deal.contact,
          opportunity: deal.id,
          kind: 'call',
          occurred_at: new Date().toISOString(),
          outcome: 'Load test call.',
          follow_up_title: 'Load test follow-up',
          follow_up_due_at: due,
        },
        jar,
      )
    }
  })

  group('manager view', () => {
    read('/reports/overview/')
    read('/tickets/?page_size=50')
  })

  sleep(1 + Math.random() * 2)
}
