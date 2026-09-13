import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * Container card supporting standard, interactive, and elevated states.
 *
 * Provides styling variants for subtle hover boundaries (interactive)
 * and elevated surface shadows for prominent active controls.
 */
function Card({
  className,
  size = 'default',
  interactive = false,
  elevated = false,
  ...props
}: React.ComponentProps<'div'> & {
  size?: 'default' | 'sm';
  interactive?: boolean;
  elevated?: boolean;
}) {
  return (
    <div
      data-slot="card"
      data-size={size}
      data-interactive={interactive ? 'true' : undefined}
      data-elevated={elevated ? 'true' : undefined}
      className={cn(
        'flex flex-col gap-6 overflow-hidden rounded-2xl bg-card py-6 text-sm text-card-foreground',
        'border border-border',
        size === 'sm' && 'gap-4 py-4',
        interactive &&
          'transition-all duration-200 hover:border-foreground/30 hover:shadow-[0_1px_2px_rgba(0,0,0,0.04)]',
        elevated &&
          'shadow-[0_1px_2px_rgba(0,0,0,0.04),0_1px_3px_1px_rgba(0,0,0,0.06)]',
        className,
      )}
      {...props}
    />
  );
}

function CardHeader({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="card-header"
      className={cn(
        'grid auto-rows-min items-start gap-2 px-6 has-data-[slot=card-action]:grid-cols-[1fr_auto] has-data-[slot=card-description]:grid-rows-[auto_auto] [.border-b]:pb-6 group-data-[size=sm]/card:px-4 group-data-[size=sm]/card:[.border-b]:pb-4',
        className,
      )}
      {...props}
    />
  );
}

function CardTitle({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="card-title"
      className={cn('text-base font-semibold leading-none', className)}
      {...props}
    />
  );
}

function CardDescription({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="card-description"
      className={cn('text-sm text-muted-foreground', className)}
      {...props}
    />
  );
}

function CardAction({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="card-action"
      className={cn(
        'col-start-2 row-span-2 row-start-1 self-start justify-self-end',
        className,
      )}
      {...props}
    />
  );
}

function CardContent({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="card-content"
      className={cn('px-6 group-data-[size=sm]/card:px-4', className)}
      {...props}
    />
  );
}

function CardFooter({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="card-footer"
      className={cn(
        'flex items-center px-6 group-data-[size=sm]/card:px-4 [.border-t]:pt-6 group-data-[size=sm]/card:[.border-t]:pt-4',
        className,
      )}
      {...props}
    />
  );
}

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardAction,
  CardDescription,
  CardContent,
};
