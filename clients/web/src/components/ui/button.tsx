import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded font-label-md text-label-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-surface disabled:pointer-events-none disabled:opacity-50 active:scale-[0.98] duration-150 [&_.material-symbols-outlined]:text-[18px]',
  {
    variants: {
      variant: {
        default: 'bg-primary text-on-primary hover:bg-primary-container',
        accent: 'bg-on-tertiary-container text-on-tertiary hover:bg-tertiary-container',
        destructive: 'bg-error text-on-error hover:bg-error/90',
        outline:
          'border border-outline-variant/60 bg-surface text-on-surface hover:bg-surface-container-high',
        secondary:
          'bg-surface-container-high text-on-surface hover:bg-surface-container-highest',
        ghost: 'text-on-surface-variant hover:bg-surface-container-high',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 px-3',
        lg: 'h-10 px-6',
        icon: 'h-10 w-10 rounded-full',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    )
  },
)
Button.displayName = 'Button'

export { buttonVariants }
