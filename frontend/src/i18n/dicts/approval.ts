const en = {
  'approval.title': 'Approve tool call',
  'approval.arguments': 'Arguments',
  'approval.alwaysAutoApprove': 'Always auto-approve',
  'approval.reject': 'Reject',
  'approval.approve': 'Approve',
} as const

const fa: Record<keyof typeof en, string> = {
  'approval.title': 'تأیید فراخوانی ابزار',
  'approval.arguments': 'آرگومان‌ها',
  'approval.alwaysAutoApprove': 'همیشه به‌صورت خودکار تأیید شود',
  'approval.reject': 'رد',
  'approval.approve': 'تأیید',
}

export const approval = { en, fa }
