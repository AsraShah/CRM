import { Link } from 'react-router'

import { Dialog } from '@/components/Dialog'
import ui from '@/components/ui.module.css'
import { useMeasureRecords } from '@/lib/queries'
import { recordLink } from '@/features/today/AlertItem'

/**
 * The records behind one figure (CRM11): "every figure shall expose its
 * filter, time period and underlying records". The count shown here comes
 * from the same definition as the dashboard figure, so the two reconcile.
 */
export function MeasureDialog({
  workspaceId,
  measure,
  title,
  period,
  onClose,
}: {
  workspaceId: string
  measure: string
  title: string
  period: Record<string, string>
  onClose: () => void
}) {
  const { data, isLoading, isError } = useMeasureRecords(workspaceId, measure, period)

  return (
    <Dialog
      title={title}
      context={data ? `${data.period.start} to ${data.period.end} · ${data.count} records` : undefined}
      onClose={onClose}
    >
      {isLoading ? (
        <p role="status">Loading records…</p>
      ) : isError || !data ? (
        <p role="alert">The records could not be loaded.</p>
      ) : data.records.length === 0 ? (
        <p className={ui.muted}>No records behind this figure.</p>
      ) : (
        <>
          <ul className={ui.list}>
            {data.records.map((record) => {
              const link = recordLink(record.type, record.id)
              return (
                <li key={record.id} style={{ fontSize: 'var(--text-sm)' }}>
                  {link ? <Link to={link}>{record.label}</Link> : <strong>{record.label}</strong>}
                  <div className={ui.muted}>{record.detail}</div>
                </li>
              )
            })}
          </ul>
          {data.shown < data.count ? (
            <p className={ui.hint}>
              Showing the latest {data.shown} of {data.count}.
            </p>
          ) : null}
        </>
      )}
      <div className={ui.actionsEnd} style={{ marginTop: 'var(--space-3)' }}>
        <button type="button" className={ui.secondary} onClick={onClose}>
          Close
        </button>
      </div>
    </Dialog>
  )
}
