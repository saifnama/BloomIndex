/**
 * Button and tooltip composition for accessible icon triggers.
 *
 * Renders an accessible Radix tooltip on hover/focus while forwarding
 * props to support composition under asChild primitives.
 */

import { type ComponentProps, type FC, type ReactNode } from 'react';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Button } from '@/components/ui/button';

type Side = 'top' | 'right' | 'bottom' | 'left';

interface TooltipIconButtonProps extends ComponentProps<typeof Button> {
  tooltip: string;
  side?: Side;
  children: ReactNode;
}

export const TooltipIconButton: FC<TooltipIconButtonProps> = ({
  tooltip,
  side = 'bottom',
  children,
  ...props
}) => {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button {...props}>
          {children}
          <span className="sr-only">{tooltip}</span>
        </Button>
      </TooltipTrigger>
      <TooltipContent side={side}>{tooltip}</TooltipContent>
    </Tooltip>
  );
};
