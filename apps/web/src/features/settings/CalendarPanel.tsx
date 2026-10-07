import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { TextArea, TextInput } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import ui from '@/components/ui.module.css'
import { LIMITS, validators } from '@/lib/limits'
import { api } from '@/lib/api'
import type { SessionContext } from '@/lib/types'

/** One YYYY-MM-DD date per line, at most one per day of the year. */
function holidayDates(value: string): string | null {
  const lines = value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  if (lines.length > 366) return 'List at most 366 holidays.'
  const bad = lines.find(
    (line) => !/^\d{4}-\d{2}-\d{2}$/.test(line) || Number.isNaN(Date.parse(`${line}T00:00:00`)),
  )
  return bad ? `"${bad}" is not a date. Use YYYY-MM-DD, one per line.` : null
}

interface CalendarSettings {
  time_zone: string
  working_weekdays: number[]
  working_intervals: { start: string; end: string }[]
  holidays: string[]
  default_currency: string
  calendar_version: number
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/**
 * The working calendar (CRM05, CRM06).
 *
 * Every deadline delay and rule timing is counted in this calendar's working
 * time. A change bumps the calendar version; deadlines already set keep the
 * version that produced them, so nothing already promised moves.
 */
export function CalendarPanel({ session }: { session: SessionContext }) {
  const ws = session.workspace.id
  const { data, isLoading } = useQuery({
    queryKey: [ws, 'workspace-settings'] as const,
    queryFn: () => api.get<CalendarSettings>('/workspace/settings/'),
  })
  if (isLoading || !data) return <p role="status">Loading the calendar…</p>
  return <CalendarForm key={data.calendar_version} settings={data} workspaceId={ws} />
}

function CalendarForm({ settings, workspaceId }: { settings: CalendarSettings; workspaceId: string }) {
  const client = useQueryClient()
  const [timeZone, setTimeZone] = useState(settings.time_zone)
  const [weekdays, setWeekdays] = useState<number[]>(settings.working_weekdays)
  const [intervals, setIntervals] = useState(settings.working_intervals)
  const [holidays, setHolidays] = useState(settings.holidays.join('\n'))
  const [currency, setCurrency] = useState(settings.default_currency)

  const save = useMutation({
    mutationFn: () =>
      api.put<CalendarSettings>('/workspace/settings/', {
        time_zone: timeZone.trim(),
        working_weekdays: weekdays,
        working_intervals: intervals,
        holidays: holidays
          .split(/[\s,]+/)
          .map((day) => day.trim())
          .filter(Boolean),
        default_currency: currency.trim().toUpperCase(),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [workspaceId, 'workspace-settings'] })
      void client.invalidateQueries({ queryKey: ['session'] })
    },
  })

  function toggleDay(day: number) {
    setWeekdays((current) =>
      current.includes(day) ? current.filter((d) => d !== day) : [...current, day].sort(),
    )
  }

  return (
    <section aria-labelledby="calendar-heading" className={ui.card}>
      <h2 id="calendar-heading" className={ui.sectionTitle}>
        Working calendar
      </h2>
      <p className={ui.muted}>
        Deadlines and rule timings count only working time. Changing this does
        not move deadlines already set. Version {settings.calendar_version}.
      </p>
      <form
        className={ui.form}
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate()
        }}
      >
        <div className={ui.formGrid}>
          <label className={ui.field}>
            <span>Time zone</span>
            <span className={ui.hint}>IANA name, for example Asia/Karachi.</span>
            <TextInput
              limit={LIMITS.timeZone}
              validate={validators.timeZone}
              type="text" required
              value={timeZone}
              onChange={setTimeZone}
            />
          </label>
          <label className={ui.field}>
            <span>Default currency</span>
            <span className={ui.hint}>Three-letter code.</span>
            <TextInput
              limit={LIMITS.currency}
              validate={validators.currency}
              type="text"
              required
              pattern="[A-Za-z]{3}"
              value={currency}
              onChange={(value) => setCurrency(value.toUpperCase())}
            />
          </label>
        </div>
        <fieldset className={ui.fieldset}>
          <legend>Working days</legend>
          <div className={ui.actions}>
            {DAYS.map((label, day) => (
              <label key={label} className={ui.checkbox}>
                <input type="checkbox" checked={weekdays.includes(day)} onChange={() => toggleDay(day)} />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className={ui.fieldset}>
          <legend>Working hours</legend>
          {intervals.map((interval, index) => (
            <div key={index} className={ui.actions} style={{ alignItems: 'end' }}>
              <label className={ui.field}>
                <span>From</span>
                <input
                  type="time"
                  required
                  value={interval.start}
                  onChange={(e) =>
                    setIntervals((all) => all.map((x, i) => (i === index ? { ...x, start: e.target.value } : x)))
                  }
                />
              </label>
              <label className={ui.field}>
                <span>To</span>
                <input
                  type="time"
                  required
                  value={interval.end}
                  onChange={(e) =>
                    setIntervals((all) => all.map((x, i) => (i === index ? { ...x, end: e.target.value } : x)))
                  }
                />
              </label>
              {intervals.length > 1 ? (
                <button
                  type="button"
                  className={ui.secondary}
                  onClick={() => setIntervals((all) => all.filter((_, i) => i !== index))}
                >
                  Remove
                </button>
              ) : null}
            </div>
          ))}
          <div>
            <button
              type="button"
              className={ui.secondary}
              onClick={() => setIntervals((all) => [...all, { start: '14:00', end: '18:00' }])}
            >
              Add a period
            </button>
          </div>
        </fieldset>
        <label className={ui.field}>
          <span>Holidays</span>
          <span className={ui.hint}>One date per line, YYYY-MM-DD.</span>
          <TextArea
            limit={4100}
            validate={holidayDates}
            rows={4}
            value={holidays}
            onChange={setHolidays}
          />
        </label>
        <ErrorNotice error={save.error} fallback="The calendar could not be saved." />
        {save.isSuccess ? (
          <p className={ui.success} role="status">
            Saved as calendar version {save.data.calendar_version}.
          </p>
        ) : null}
        <div className={ui.actions}>
          <button type="submit" className={ui.primary} disabled={save.isPending}>
            {save.isPending ? 'Saving…' : 'Save calendar'}
          </button>
        </div>
      </form>
    </section>
  )
}
