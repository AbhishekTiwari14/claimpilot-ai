import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-semibold transition-colors disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-teal-800',
        outline: 'border border-border bg-white text-foreground hover:bg-slate-50',
        ghost: 'text-foreground hover:bg-slate-100',
        destructive: 'border border-red-200 bg-white text-red-800 hover:bg-red-50',
      },
      size: { default: 'min-h-10 px-4 py-2', sm: 'min-h-8 px-3 py-1', icon: 'size-9' },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
);

export function Button({ className, variant, size, asChild, ...props }: ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Component = asChild ? Slot : 'button';
  return <Component className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
