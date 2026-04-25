export interface BrandStyle {
  initial: string
  fg: string
  bg: string
  gradient: string
  ring: string
}

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
    fg: 'text-white',
    bg: 'bg-blue-600',
    gradient: 'from-blue-500 to-blue-700',
    ring: 'ring-blue-400/40',
  },
  linear: {
    initial: 'L',
    fg: 'text-white',
    bg: 'bg-indigo-600',
    gradient: 'from-indigo-500 to-violet-700',
    ring: 'ring-indigo-400/40',
  },
  github: {
    initial: 'G',
    fg: 'text-white',
    bg: 'bg-neutral-800',
    gradient: 'from-neutral-700 to-neutral-900',
    ring: 'ring-white/20',
  },
  slack: {
    initial: 'S',
    fg: 'text-white',
    bg: 'bg-fuchsia-600',
    gradient: 'from-fuchsia-500 to-purple-700',
    ring: 'ring-fuchsia-400/40',
  },
  notion: {
    initial: 'N',
    fg: 'text-neutral-900',
    bg: 'bg-neutral-100',
    gradient: 'from-neutral-100 to-neutral-300',
    ring: 'ring-white/40',
  },
  confluence: {
    initial: 'C',
    fg: 'text-white',
    bg: 'bg-sky-600',
    gradient: 'from-sky-500 to-blue-700',
    ring: 'ring-sky-400/40',
  },
  gmail: {
    initial: 'M',
    fg: 'text-white',
    bg: 'bg-red-600',
    gradient: 'from-red-500 to-rose-700',
    ring: 'ring-red-400/40',
  },
  googlecalendar: {
    initial: 'C',
    fg: 'text-white',
    bg: 'bg-blue-500',
    gradient: 'from-blue-400 to-cyan-600',
    ring: 'ring-blue-300/40',
  },
  figma: {
    initial: 'F',
    fg: 'text-white',
    bg: 'bg-pink-600',
    gradient: 'from-pink-500 via-orange-500 to-amber-500',
    ring: 'ring-pink-400/40',
  },
}

export function brandFor(name: string): BrandStyle {
  return REGISTRY[name.toLowerCase()] ?? FALLBACK
}
