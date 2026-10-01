import type { ReactNode } from 'react'

export interface BrandStyle {
  initial: string
  slug?: string
  logo?: ReactNode
  fg: string
  bg: string
  gradient: string
  ring: string
}

const SlackLogo = (
  <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden>
    <path
      fill="#E01E5A"
      d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z"
    />
    <path
      fill="#36C5F0"
      d="M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312z"
    />
    <path
      fill="#2EB67D"
      d="M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312z"
    />
    <path
      fill="#ECB22E"
      d="M15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z"
    />
  </svg>
)

const OutlookLogo = (
  <svg viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg" aria-hidden>
    <path d="M7.88 12.04q0 .45-.11.87-.1.41-.33.74-.22.33-.55.52-.33.2-.78.2-.45 0-.78-.2-.33-.2-.55-.52-.22-.33-.33-.74-.1-.42-.1-.87 0-.46.1-.87.11-.41.33-.73.22-.33.55-.52.33-.19.78-.19.45 0 .78.19.33.19.55.52.23.32.33.73.11.41.11.87zM24 12v9.38q0 .46-.33.8-.33.32-.8.32H7.13q-.46 0-.8-.33-.32-.33-.32-.8V18H1q-.41 0-.7-.3-.3-.29-.3-.7V7q0-.41.3-.7Q.58 6 1 6h6.5V2.55q0-.44.3-.75.3-.3.75-.3h12.9q.44 0 .75.3.3.3.3.75V10.85l1.24.72h.01q.1.07.18.18.07.12.07.25zm-6-8.25v3h3v-3zm0 4.5v3h3v-3zm0 4.5v1.83l3.05-1.83zm-5.25-9v3h3.75v-3zm0 4.5v3h3.75v-3zm0 4.5v2.03l2.41 1.5 1.34-.8v-2.73zM9 3.75V6h2l.13.01.12.04v-2.3zM5.98 15.98q.9 0 1.6-.3.7-.3 1.2-.85.5-.55.74-1.3.25-.74.25-1.61 0-.83-.25-1.55-.24-.71-.71-1.24t-1.15-.83q-.68-.3-1.55-.3-.92 0-1.64.3-.71.3-1.2.85-.5.54-.75 1.3-.25.74-.25 1.6 0 .83.24 1.55.25.72.71 1.25.46.53 1.13.84.67.3 1.55.3zM7.5 21h12.39L12 16.08V17q0 .41-.3.7-.29.3-.7.3H7.5zm15-.13v-7.24l-5.9 3.54Z" />
  </svg>
)

const FALLBACK: BrandStyle = {
  initial: '?',
  fg: 'text-neutral-100',
  bg: 'bg-neutral-700',
  gradient: 'from-neutral-600 to-neutral-800',
  ring: 'ring-white/10',
}

const REGISTRY: Record<string, BrandStyle> = {
  jira: {
    initial: 'J',
    slug: 'jira',
    fg: 'text-white',
    bg: 'bg-blue-600',
    gradient: 'from-blue-500 to-blue-700',
    ring: 'ring-blue-400/40',
  },
  linear: {
    initial: 'L',
    slug: 'linear',
    fg: 'text-white',
    bg: 'bg-indigo-600',
    gradient: 'from-indigo-500 to-violet-700',
    ring: 'ring-indigo-400/40',
  },
  github: {
    initial: 'G',
    slug: 'github',
    fg: 'text-white',
    bg: 'bg-neutral-800',
    gradient: 'from-neutral-700 to-neutral-900',
    ring: 'ring-white/20',
  },
  slack: {
    initial: 'S',
    logo: SlackLogo,
    fg: 'text-white',
    bg: 'bg-fuchsia-600',
    gradient: 'from-fuchsia-500 to-purple-700',
    ring: 'ring-fuchsia-400/40',
  },
  notion: {
    initial: 'N',
    slug: 'notion',
    fg: 'text-neutral-900',
    bg: 'bg-neutral-100',
    gradient: 'from-neutral-100 to-neutral-300',
    ring: 'ring-white/40',
  },
  confluence: {
    initial: 'C',
    slug: 'confluence',
    fg: 'text-white',
    bg: 'bg-sky-600',
    gradient: 'from-sky-500 to-blue-700',
    ring: 'ring-sky-400/40',
  },
  gmail: {
    initial: 'M',
    slug: 'gmail',
    fg: 'text-white',
    bg: 'bg-red-600',
    gradient: 'from-red-500 to-rose-700',
    ring: 'ring-red-400/40',
  },
  googlecalendar: {
    initial: 'C',
    slug: 'googlecalendar',
    fg: 'text-white',
    bg: 'bg-blue-500',
    gradient: 'from-blue-400 to-cyan-600',
    ring: 'ring-blue-300/40',
  },
  googlesheets: {
    initial: 'S',
    slug: 'googlesheets',
    fg: 'text-white',
    bg: 'bg-emerald-600',
    gradient: 'from-emerald-500 to-green-700',
    ring: 'ring-emerald-400/40',
  },
  outlook: {
    initial: 'O',
    logo: OutlookLogo,
    fg: 'text-white',
    bg: 'bg-sky-600',
    gradient: 'from-sky-500 to-blue-700',
    ring: 'ring-sky-400/40',
  },
  figma: {
    initial: 'F',
    slug: 'figma',
    fg: 'text-white',
    bg: 'bg-pink-600',
    gradient: 'from-pink-500 via-orange-500 to-amber-500',
    ring: 'ring-pink-400/40',
  },
}

export function brandFor(name: string): BrandStyle {
  return REGISTRY[name.toLowerCase()] ?? FALLBACK
}

export function brandLogoUrl(slug: string, color = 'white'): string {
  return `https://cdn.simpleicons.org/${slug}/${color}`
}
