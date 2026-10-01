import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import { Toaster } from '../components/Toaster'
import { useAppStore } from '../store'

describe('Toaster', () => {
  beforeEach(() => {
    useAppStore.setState({ toasts: [] })
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('renders nothing when there are no toasts', () => {
    render(<Toaster />)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('renders a pushed toast with its text', () => {
    render(<Toaster />)
    act(() => {
      useAppStore.getState().pushToast({ kind: 'error', text: 'boom' })
    })
    const region = screen.getByTestId('toaster')
    expect(region).toHaveTextContent('boom')
    expect(region).toHaveAttribute('aria-live', 'polite')
  })

  it('auto-dismisses a toast after the timeout', () => {
    render(<Toaster />)
    act(() => {
      useAppStore.getState().pushToast({ kind: 'info', text: 'transient' })
    })
    expect(screen.getByText('transient')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(6_500)
    })
    expect(screen.queryByText('transient')).toBeNull()
  })

  it('dismisses a toast when the close button is clicked', () => {
    render(<Toaster />)
    act(() => {
      useAppStore.getState().pushToast({ kind: 'error', text: 'dismiss me' })
    })
    const closeBtn = screen.getByTestId('toast-dismiss')
    act(() => {
      fireEvent.click(closeBtn)
    })
    expect(screen.queryByText('dismiss me')).toBeNull()
  })
})
