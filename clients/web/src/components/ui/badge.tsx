import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-label-sm uppercase whitespace-nowrap border',
  {
    variants: {
      variant: {
        neutral:
          'bg-surface-container-highest text-secondary border-transparent',
        critical: 'bg-error/10 text-error border-error/20',
        warning: 'bg-on-tertiary-container/10 text-on-tertiary-container border-on-tertiary-container/20',
        success: 'bg-success/10 text-success border-success/25',
        outline: 'bg-surface text-on-surface-variant border-outline-variant/40',
        solid: 'bg-tertiary-container text-on-tertiary border-transparent',
      },
    },
    defaultVariants: {
      variant: 'neutral',
    },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { badgeVariants }
