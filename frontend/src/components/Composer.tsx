import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'

export interface ComposerProps {
  onSend(text: string): void | Promise<void>
  disabled?: boolean
  placeholder?: string
}

export function Composer({
  onSend,
  disabled = false,
  placeholder = 'Send a message...',
}: ComposerProps) {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    const max = 240
    el.style.height = `${Math.min(el.scrollHeight, max)}px`
  }, [text])

  const submit = async () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    setText('')
    await onSend(trimmed)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void submit()
    }
  }

  return (
    <div className="border-t border-white/5 bg-white/[0.02] px-4 py-3">
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          data-testid="composer-textarea"
          className={clsx(
            'glass-input flex-1 resize-none rounded-lg px-3 py-2 text-sm text-neutral-100',
            'placeholder:text-neutral-500',
            'disabled:opacity-50',
          )}
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={disabled || !text.trim()}
          data-testid="composer-send"
          className={clsx(
            'rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 px-4 py-2 text-sm font-medium text-white',
            'shadow-lg shadow-blue-500/30 ring-1 ring-blue-300/20',
            'hover:from-blue-400 hover:to-blue-500 transition disabled:cursor-not-allowed disabled:opacity-40',
          )}
        >
          Send
        </button>
      </div>
    </div>
  )
}

export default Composer
