import {
  useEffect,
  useId,
  useRef,
  useState,
  type InputHTMLAttributes,
  type Ref,
  type RefCallback,
  type TextareaHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'

import ui from './ui.module.css'
import { formatCount, type Validator } from '@/lib/limits'

/**
 * Text fields with the checks every input needs (SVX-TECH-001 section 7.3):
 *
 * - a character ceiling matching the API, enforced while typing;
 * - a live counter on long text, so the limit is visible before it is hit;
 * - required text that is only spaces is refused, not sent;
 * - an optional validator for structured values (phone, currency, codes).
 *
 * Problems are reported through the browser's own constraint validation, so
 * the form will not submit and the first invalid field receives focus. The
 * message is also shown under the field and linked with aria-describedby.
 *
 * The counter and message sit inside the field's <label>, so they are
 * aria-hidden there (otherwise they would become part of the field's name)
 * and announced through aria-describedby instead.
 */

const BLANK_MESSAGE = 'Enter some text, not only spaces.'

/** Point the internal ref and a caller's ref (React 19 passes ref as a prop) at one node. */
function mergeRefs<T>(...refs: (Ref<T> | undefined)[]): RefCallback<T> {
  return (node) => {
    for (const ref of refs) {
      if (typeof ref === 'function') ref(node)
      else if (ref) (ref as { current: T | null }).current = node
    }
  }
}

function describedBy(own: string, extra: string | undefined): string {
  return extra ? `${extra} ${own}` : own
}

function useFieldValidity<T extends HTMLInputElement | HTMLTextAreaElement>(
  value: string,
  required: boolean | undefined,
  validate: Validator | undefined,
) {
  const ref = useRef<T>(null)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    let problem: string | null = null
    if (required && value.length > 0 && value.trim() === '') problem = BLANK_MESSAGE
    else if (validate) problem = validate(value)
    ref.current?.setCustomValidity(problem ?? '')
    setMessage(problem)
  }, [value, required, validate])

  return { ref, message }
}

function Feedback({
  id,
  length,
  limit,
  showCount,
  message,
  touched,
}: {
  id: string
  length: number
  limit: number | undefined
  showCount: boolean
  message: string | null
  touched: boolean
}) {
  const near = limit !== undefined && length >= limit * 0.9
  const visibleMessage = touched ? message : null
  const description = [visibleMessage, limit !== undefined ? `Up to ${limit} characters.` : null]
    .filter(Boolean)
    .join(' ')
  return (
    <>
      {/* Visible feedback is drawn with CSS generated content (data-text), so
          it never becomes part of the surrounding <label>'s text: the
          field's name stays exactly its label, for people and for tools. */}
      {showCount || visibleMessage ? (
        <span className={ui.fieldFeedback} aria-hidden="true">
          <span className={ui.fieldError} data-text={visibleMessage ?? ''} />
          {showCount && limit !== undefined ? (
            <span
              className={near ? `${ui.counter} ${ui.counterNear}` : ui.counter}
              data-text={formatCount(length, limit)}
            />
          ) : null}
        </span>
      ) : null}
      {/* What assistive technology reads, outside the label. */}
      {typeof document === 'undefined'
        ? null
        : createPortal(
            <span id={id} className="visually-hidden">
              {description}
            </span>,
            document.body,
          )}
    </>
  )
}

type TextInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'> & {
  value: string
  onChange: (value: string) => void
  /** Character ceiling; mirrors the API. */
  limit?: number
  validate?: Validator
  /** Show the counter even on a short field. Off by default for inputs. */
  showCount?: boolean
  ref?: Ref<HTMLInputElement>
}

export function TextInput({
  value,
  onChange,
  limit,
  validate,
  showCount,
  required,
  type = 'text',
  onBlur,
  ref: externalRef,
  "aria-describedby": extraDescribedBy,
  ...rest
}: TextInputProps) {
  const { ref, message } = useFieldValidity<HTMLInputElement>(value, required, validate)
  const [touched, setTouched] = useState(false)
  const feedbackId = useId()
  // Inputs show the count once the text gets close to the ceiling.
  const counted = Boolean(showCount) || (limit !== undefined && value.length >= limit * 0.8)
  return (
    <>
      <input
        ref={mergeRefs(ref, externalRef)}
        type={type}
        value={value}
        required={required}
        maxLength={limit}
        aria-invalid={touched && message ? true : undefined}
        aria-describedby={describedBy(feedbackId, extraDescribedBy)}
        onChange={(event) => onChange(event.target.value)}
        onBlur={(event) => {
          setTouched(true)
          onBlur?.(event)
        }}
        onInvalid={() => setTouched(true)}
        {...rest}
      />
      <Feedback
        id={feedbackId}
        length={value.length}
        limit={limit}
        showCount={counted}
        message={message}
        touched={touched}
      />
    </>
  )
}

type TextAreaProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange'> & {
  value: string
  onChange: (value: string) => void
  /** Character ceiling; mirrors the API. Long text always shows its count. */
  limit: number
  validate?: Validator
  ref?: Ref<HTMLTextAreaElement>
}

export function TextArea({
  value,
  onChange,
  limit,
  validate,
  required,
  rows = 3,
  onBlur,
  ref: externalRef,
  "aria-describedby": extraDescribedBy,
  ...rest
}: TextAreaProps) {
  const { ref, message } = useFieldValidity<HTMLTextAreaElement>(value, required, validate)
  const [touched, setTouched] = useState(false)
  const feedbackId = useId()
  return (
    <>
      <textarea
        ref={mergeRefs(ref, externalRef)}
        rows={rows}
        value={value}
        required={required}
        maxLength={limit}
        aria-invalid={touched && message ? true : undefined}
        aria-describedby={describedBy(feedbackId, extraDescribedBy)}
        onChange={(event) => onChange(event.target.value)}
        onBlur={(event) => {
          setTouched(true)
          onBlur?.(event)
        }}
        onInvalid={() => setTouched(true)}
        {...rest}
      />
      <Feedback
        id={feedbackId}
        length={value.length}
        limit={limit}
        showCount
        message={message}
        touched={touched}
      />
    </>
  )
}
