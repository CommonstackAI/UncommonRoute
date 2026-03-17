import { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "bg-white rounded-2xl shadow-[0_2px_12px_-4px_rgba(0,0,0,0.04),0_1px_3px_rgba(0,0,0,0.02)] ring-1 ring-black/[0.04] overflow-hidden",
        className
      )}
      {...props}
    />
  );
}
