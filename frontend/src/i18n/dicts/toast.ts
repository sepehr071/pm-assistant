const en = {
  'toast.renameFailed': 'Could not rename chat: {error}',
  'toast.deleteFailed': 'Could not delete chat: {error}',
  'toast.streamError': 'Chat error: {error}',
  'toast.turnInProgress':
    'A response is still streaming in this chat — wait for it to finish.',
} as const

const fa: Record<keyof typeof en, string> = {
  'toast.renameFailed': 'تغییر نام گفتگو ممکن نشد: {error}',
  'toast.deleteFailed': 'حذف گفتگو ممکن نشد: {error}',
  'toast.streamError': 'خطای گفتگو: {error}',
  'toast.turnInProgress':
    'یک پاسخ هنوز در این گفتگو در حال استریم است — تا پایان آن صبر کنید.',
}

export const toast = { en, fa }
