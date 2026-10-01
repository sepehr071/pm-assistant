import clsx from 'clsx'
import { brandFor, brandLogoUrl } from './brandRegistry'

interface BrandGlyphProps {
  name: string
  className?: string
  imgClassName?: string
  fontClassName?: string
  color?: string
}

export function BrandGlyph({
  name,
  className,
  imgClassName = 'h-1/2 w-1/2',
  fontClassName,
  color = 'white',
}: BrandGlyphProps) {
  const b = brandFor(name)
  return (
    <span
      aria-hidden
      className={clsx(
        'inline-flex shrink-0 items-center justify-center rounded-md bg-gradient-to-br ring-1',
        b.gradient,
        b.fg,
        b.ring,
        className,
      )}
    >
      {b.logo ? (
        <span className={clsx('block', imgClassName)}>{b.logo}</span>
      ) : b.slug ? (
        <img
          src={brandLogoUrl(b.slug, color)}
          alt=""
          className={clsx('object-contain', imgClassName)}
          loading="lazy"
          draggable={false}
        />
      ) : (
        <span className={clsx('font-bold leading-none', fontClassName)}>
          {b.initial}
        </span>
      )}
    </span>
  )
}
