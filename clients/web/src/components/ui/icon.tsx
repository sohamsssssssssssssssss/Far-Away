import { cn } from '@/lib/utils'

interface IconProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Material Symbols Outlined ligature name, e.g. "dashboard" */
  name: string
  /** Render the filled variant */
  filled?: boolean
}

/**
 * Material Symbols Outlined icon. Sizing is controlled with a `text-[..]`
 * utility (the glyph inherits `font-size`), colour with `text-*`.
 */
export function Icon({ name, filled, className, ...props }: IconProps) {
  return (
    <span
      aria-hidden="true"
      className={cn('material-symbols-outlined', filled && 'is-filled', className)}
      {...props}
    >
      {name}
    </span>
  )
}
