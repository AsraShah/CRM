import { X } from 'lucide-react'
import { useEffect, useId, useRef, type ReactNode } from 'react'

import styles from './ui.module.css'

interface Props {
  title: string
  /** One line under the title saying which record this acts on. */
  context?: string | undefined
  onClose: () => void
  children: ReactNode
}

/**
 * A modal dialog.
 *
 * Focus moves into the dialog on open and returns to whatever opened it on
 * close, Escape closes it, and Tab is kept inside it — without those, a
 * keyboard user lands behind the backdrop with no way back.
 */
export function Dialog({ title, context, onClose, children }: Props) {
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  // Held in a ref so the effect runs once. Callers pass inline arrows; were it a
  // dependency, every keystroke would re-run the effect and steal focus back
  // to the first field.
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const dialog = dialogRef.current
    const first = dialog?.querySelector<HTMLElement>(
      'input, select, textarea, button:not([data-close])',
    )
    ;(first ?? dialog)?.focus()

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab' || !dialog) return
      const focusable = [
        ...dialog.querySelectorAll<HTMLElement>(
          'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)',
        ),
      ]
      if (focusable.length === 0) return
      const firstEl = focusable[0]
      const lastEl = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === firstEl) {
        event.preventDefault()
        lastEl?.focus()
      } else if (!event.shiftKey && document.activeElement === lastEl) {
        event.preventDefault()
        firstEl?.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      opener?.focus()
    }
  }, [])

  return (
    <div className={styles.backdrop} onMouseDown={onClose}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={styles.dialog}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className={styles.dialogHeader}>
          <div>
            <h2 id={titleId}>{title}</h2>
            {context ? <p className={styles.dialogContext}>{context}</p> : null}
          </div>
          {/* data-close keeps initial focus on the first field, not on this. */}
          <button
            type="button"
            data-close
            className={styles.iconButton}
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
