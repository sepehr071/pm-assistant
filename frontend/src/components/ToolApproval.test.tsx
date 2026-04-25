import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolApproval } from './ToolApproval'
import type { PendingApproval } from '../types'

const pending: PendingApproval = {
  tool_call_id: 'call_42',
  tool_name: 'jira__create_issue',
  arguments: { summary: 'Investigate bug', priority: 'High' },
}

describe('ToolApproval', () => {
  it('renders the formatted tool name and pretty-printed arguments', () => {
    render(
      <ToolApproval
        pending={pending}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByTestId('tool-approval-name')).toHaveTextContent(
      'jira / create_issue',
    )
    const args = screen.getByTestId('tool-approval-args')
    expect(args).toHaveTextContent('Investigate bug')
    expect(args).toHaveTextContent('priority')
  })

  it('calls onApprove with alwaysApprove=false by default', async () => {
    const onApprove = vi.fn()
    const onReject = vi.fn()
    render(
      <ToolApproval
        pending={pending}
        onApprove={onApprove}
        onReject={onReject}
      />,
    )
    await userEvent.click(screen.getByTestId('tool-approval-approve'))
    expect(onApprove).toHaveBeenCalledTimes(1)
    expect(onApprove).toHaveBeenCalledWith('call_42', false)
    expect(onReject).not.toHaveBeenCalled()
  })

  it('passes alwaysApprove=true when checkbox is ticked', async () => {
    const onApprove = vi.fn()
    render(
      <ToolApproval
        pending={pending}
        onApprove={onApprove}
        onReject={vi.fn()}
      />,
    )
    await userEvent.click(screen.getByTestId('tool-approval-always'))
    await userEvent.click(screen.getByTestId('tool-approval-approve'))
    expect(onApprove).toHaveBeenCalledWith('call_42', true)
  })

  it('calls onReject when the Reject button is clicked', async () => {
    const onReject = vi.fn()
    render(
      <ToolApproval
        pending={pending}
        onApprove={vi.fn()}
        onReject={onReject}
      />,
    )
    await userEvent.click(screen.getByTestId('tool-approval-reject'))
    expect(onReject).toHaveBeenCalledWith('call_42')
  })
})
