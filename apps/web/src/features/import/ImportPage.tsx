import { ArrowRight, CheckCircle2, ChevronLeft, Download, FileSpreadsheet, UploadCloud } from 'lucide-react'
import { useState } from 'react'
import { Link, useOutletContext } from 'react-router'

import styles from './ImportPage.module.css'
import { TextInput } from '@/components/fields'
import { PageHeader, Stat } from '@/components/kit'
import ui from '@/components/ui.module.css'
import { LIMITS, validators } from '@/lib/limits'
import { ApiError } from '@/lib/api'
import { useCommitImport, usePreviewImport, useUploadImport } from '@/lib/queries'
import type { ImportBatch, ImportPreview, SessionContext } from '@/lib/types'

/** Fields a spreadsheet column can be mapped onto. */
const TARGET_FIELDS = [
  { value: '', label: 'Do not import' },
  { value: 'display_name', label: 'Name (required)' },
  { value: 'company_name', label: 'Company' },
  { value: 'email', label: 'Email' },
  { value: 'phone', label: 'Phone' },
  { value: 'job_title', label: 'Job title' },
  { value: 'source', label: 'Source' },
  { value: 'owner_email', label: 'Owner email' },
  { value: 'notes', label: 'Notes' },
] as const

const STEPS = ['Choose a file', 'Map the columns', 'Review', 'Done']

/**
 * Spreadsheet migration (CRM02, Journey A).
 *
 * Upload, map, preview, confirm — in that order, and the preview never writes
 * anything. The manager sees the created, duplicate, invalid and skipped counts
 * and confirms explicitly.
 *
 * Commit is blocked when the counts do not reconcile to the input rows. An
 * import that silently loses rows is worse than one that refuses to run.
 */
export function ImportPage() {
  const session = useOutletContext<SessionContext>()
  const workspaceId = session.workspace.id

  const [batch, setBatch] = useState<ImportBatch | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [defaultCountry, setDefaultCountry] = useState('PK')
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [committed, setCommitted] = useState<ImportBatch | null>(null)
  const [dragging, setDragging] = useState(false)

  const upload = useUploadImport(workspaceId)
  const previewMutation = usePreviewImport()
  const commitMutation = useCommitImport(workspaceId)

  function uploadFile(file: File) {
    setPreview(null)
    setCommitted(null)
    upload.mutate(file, {
      onSuccess: (created) => {
        setBatch(created)
        // Pre-map columns whose header already matches a known field name.
        const guessed: Record<string, string> = {}
        for (const header of created.detected_headers) {
          const normalised = header.trim().toLowerCase()
          const match = TARGET_FIELDS.find(
            (field) =>
              field.value !== '' &&
              (field.value === normalised || field.label.toLowerCase().startsWith(normalised)),
          )
          if (match) guessed[header] = match.value
        }
        setMapping(guessed)
      },
    })
  }

  function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (file) uploadFile(file)
  }

  function handleDrop(event: React.DragEvent<HTMLLabelElement>) {
    event.preventDefault()
    setDragging(false)
    const file = event.dataTransfer.files[0]
    if (file) uploadFile(file)
  }

  function handlePreview(event: React.FormEvent) {
    event.preventDefault()
    if (!batch) return
    const columnMapping = Object.fromEntries(
      Object.entries(mapping).filter(([, target]) => target !== ''),
    )
    previewMutation.mutate(
      { batchId: batch.id, columnMapping, defaultCountry, duplicatePolicy: 'skip' },
      { onSuccess: setPreview },
    )
  }

  function handleCommit() {
    if (!preview) return
    commitMutation.mutate(
      {
        batchId: preview.batch.id,
        previewHash: preview.preview_hash,
        expectedVersion: preview.batch.version,
      },
      { onSuccess: setCommitted },
    )
  }

  const current = committed ? 3 : preview ? 2 : batch ? 1 : 0
  const nameMapped = Object.values(mapping).includes('display_name')

  return (
    <div className={ui.page}>
      <Link to="/leads" className={styles.back}>
        <ChevronLeft size={16} aria-hidden="true" />
        Leads and Deals
      </Link>
      <PageHeader
        eyebrow="Sales · Migration"
        title="Import a spreadsheet"
        subtitle="Upload a CSV, map its columns, then review what will happen before anything is written. Your original spreadsheet is never modified."
      >
        <ol className={ui.stepper} aria-label="Import progress">
          {STEPS.map((label, index) => (
            <li
              key={label}
              className={index < current ? ui.stepDone : index === current ? ui.stepActive : ui.step}
              aria-current={index === current ? 'step' : undefined}
            >
              {label}
            </li>
          ))}
        </ol>
      </PageHeader>

      <section aria-labelledby="upload-heading" className={ui.card}>
        <h2 id="upload-heading" className={styles.stepTitle}>
          <span className={styles.stepNumber}>1</span> Choose a file
        </h2>
        <label
          className={dragging ? `${styles.dropzone} ${styles.dropzoneActive}` : styles.dropzone}
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
        >
          <span className={styles.dropIcon}>
            <UploadCloud size={24} aria-hidden="true" />
          </span>
          <span className={styles.dropTitle}>
            {batch ? 'Choose a different file' : 'Drop a CSV here, or click to choose one'}
          </span>
          <span className={styles.dropHint}>CSV file (up to 5 MB, 10,000 rows)</span>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={handleFile}
            className={styles.fileInput}
          />
        </label>
        {upload.isPending ? (
          <p role="status" className={ui.muted} style={{ marginBottom: 0 }}>
            Uploading…
          </p>
        ) : null}
        {upload.isError ? (
          <p role="alert" className={ui.error} style={{ marginTop: 'var(--space-3)' }}>
            {upload.error instanceof ApiError ? upload.error.message : 'The file could not be uploaded.'}
          </p>
        ) : null}
        {batch ? (
          <p className={styles.fileLoaded}>
            <FileSpreadsheet size={18} aria-hidden="true" />
            <span>
              Loaded <strong>{batch.original_filename}</strong> with {batch.total_rows} data{' '}
              {batch.total_rows === 1 ? 'row' : 'rows'}.
            </span>
          </p>
        ) : null}
      </section>

      {batch ? (
        <section aria-labelledby="map-heading" className={ui.card}>
          <h2 id="map-heading" className={styles.stepTitle}>
            <span className={styles.stepNumber}>2</span> Map the columns
          </h2>
          <form onSubmit={handlePreview} className={ui.form}>
            <div className={ui.tableWrap}>
              <table className={ui.table}>
                <caption className="visually-hidden">
                  Each column in your file and the field it imports into.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Column in your file</th>
                    <th scope="col">
                      <span className="visually-hidden">maps to</span>
                    </th>
                    <th scope="col">Imports as</th>
                  </tr>
                </thead>
                <tbody>
                  {batch.detected_headers.map((header) => (
                    <tr key={header}>
                      <th scope="row" className={styles.mapHeader}>
                        {header}
                      </th>
                      <td className={styles.mapArrow}>
                        <ArrowRight size={16} aria-hidden="true" />
                      </td>
                      <td>
                        <label>
                          <span className="visually-hidden">{header}</span>
                          <select
                            className={styles.mapSelect}
                            value={mapping[header] ?? ''}
                            onChange={(event) =>
                              setMapping((existing) => ({
                                ...existing,
                                [header]: event.target.value,
                              }))
                            }
                          >
                            {TARGET_FIELDS.map((field) => (
                              <option key={field.value} value={field.value}>
                                {field.label}
                              </option>
                            ))}
                          </select>
                        </label>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {!nameMapped ? (
              <p className={ui.warning}>Map one column to Name: every record needs a name.</p>
            ) : null}

            <label className={ui.field} style={{ maxWidth: '32rem' }}>
              <span>Country for phone numbers written without a country code</span>
              <TextInput
                limit={LIMITS.country}
                validate={validators.country}
                type="text"
                value={defaultCountry}
                className={styles.country}
                onChange={(value) => setDefaultCountry(value.toUpperCase())}
              />
              {/* The server will not guess: a number it cannot parse with
                  confidence is kept as written and left out of matching. */}
              <span className={ui.hint}>
                Two-letter code, for example PK. Numbers that still cannot be read are kept exactly as
                written and not used to detect duplicates.
              </span>
            </label>

            <div className={ui.actions}>
              <button type="submit" className={ui.primary} disabled={previewMutation.isPending}>
                {previewMutation.isPending ? 'Checking…' : 'Preview the import'}
              </button>
            </div>
          </form>

          {previewMutation.isError ? (
            <p role="alert" className={ui.error} style={{ marginTop: 'var(--space-3)' }}>
              {previewMutation.error instanceof ApiError
                ? previewMutation.error.message
                : 'The preview could not be produced.'}
            </p>
          ) : null}
        </section>
      ) : null}

      {preview ? (
        <section aria-labelledby="review-heading" className={ui.card}>
          <h2 id="review-heading" className={styles.stepTitle}>
            <span className={styles.stepNumber}>3</span> Review before confirming
          </h2>

          <div className={styles.counts}>
            <Stat feature label="Rows in file" value={preview.total_rows} />
            <Stat
              label="Will be created"
              value={<span className={styles.success}>{preview.created}</span>}
            />
            <Stat
              label="Duplicates skipped"
              value={<span className={styles.info}>{preview.duplicate}</span>}
            />
            <Stat label="Invalid" value={<span className={styles.danger}>{preview.invalid}</span>} />
            <Stat label="Skipped" value={preview.skipped} />
          </div>

          {preview.reconciles ? (
            <p className={ui.success}>The counts account for every row in the file.</p>
          ) : (
            <p role="alert" className={ui.error}>
              These counts do not add up to the number of rows in the file, so the import cannot
              proceed. Report this to your administrator.
            </p>
          )}

          {preview.sample_rows.some((row) => row.errors.length > 0) ? (
            <div className={ui.tableWrap} style={{ marginTop: 'var(--space-4)' }}>
              <table className={ui.table}>
                <caption className={styles.caption}>Rows needing attention (first 50 shown)</caption>
                <thead>
                  <tr>
                    <th scope="col">Row</th>
                    <th scope="col">Outcome</th>
                    <th scope="col">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.sample_rows
                    .filter((row) => row.errors.length > 0)
                    .map((row) => (
                      <tr key={row.row_number}>
                        <th scope="row">{row.row_number}</th>
                        <td>
                          <span className={row.state === 'invalid' ? ui.pillDanger : ui.pill}>
                            {row.state}
                          </span>
                        </td>
                        <td>{row.errors.join('; ')}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : null}

          <div className={styles.confirmRow}>
            <button
              type="button"
              className={ui.primary}
              onClick={handleCommit}
              disabled={
                !preview.reconciles ||
                preview.created === 0 ||
                commitMutation.isPending ||
                committed !== null
              }
            >
              {commitMutation.isPending
                ? 'Confirming…'
                : `Import ${preview.created} ${preview.created === 1 ? 'record' : 'records'}`}
            </button>
            <a className={ui.ghost} href={`/api/v1/imports/${preview.batch.id}/errors.csv/`}>
              <Download size={16} aria-hidden="true" />
              Download the rows needing attention
            </a>
          </div>

          {commitMutation.isError ? (
            <p role="alert" className={ui.error} style={{ marginTop: 'var(--space-3)' }}>
              {commitMutation.error instanceof ApiError
                ? commitMutation.error.message
                : 'The import could not be confirmed.'}
            </p>
          ) : null}
        </section>
      ) : null}

      {committed ? (
        <section aria-labelledby="done-heading" className={`${ui.card} ${styles.done}`}>
          <CheckCircle2 size={28} aria-hidden="true" className={styles.doneIcon} />
          <div>
            <h2 id="done-heading" className={styles.stepTitle} style={{ marginBottom: 'var(--space-1)' }}>
              Import queued
            </h2>
            <p style={{ margin: 0 }}>
              The import is running in the background. Refresh the Leads page in a moment to see
              the new records. Confirming again will not import the rows twice.
            </p>
            <Link to="/leads" className={ui.secondary} style={{ marginTop: 'var(--space-3)' }}>
              Go to the leads
            </Link>
          </div>
        </section>
      ) : null}
    </div>
  )
}
