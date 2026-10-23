import { cn } from "cn"

function Skeleton({
  className,
  as: Component = "div",
  ...props
}: React.ComponentProps<"div"> & { as?: "div" | "span" }) {
  return (
    <Component
      aria-hidden="true"
      data-slot="skeleton"
      className={cn("block animate-pulse rounded-md bg-muted motion-reduce:animate-none", className)}
      {...props}
    />
  )
}

export { Skeleton }
