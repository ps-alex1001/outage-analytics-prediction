# Bataan Outage Dashboard

The existing frontend uses Next.js App Router, TypeScript, Tailwind CSS v4,
and npm. This foundation adds a shared shadcn/ui sidebar and four placeholder
pages. It does not need the FastAPI server to run.

## Run locally

From the `frontend` directory:

```bash
npm ci
npm run dev
```

Open http://localhost:3000.

## Pages and layout

- `src/app/layout.tsx`: shared application title, sidebar provider, toggle,
  and main content area.
- `src/components/app-sidebar.tsx`: the four Next.js links, active route
  highlighting, collapsed icon tooltips, and mobile drawer dismissal.
- `src/app/page.tsx`: Overview (`/`).
- `src/app/spatial/page.tsx`: Spatial & Event Analysis (`/spatial`).
- `src/app/forecast/page.tsx`: Outage Forecasting (`/forecast`).
- `src/app/scenario/page.tsx`: Scenario Simulator (`/scenario`), marked planned.
- `src/app/globals.css`: neutral colors and semantic CSS variables for future
  themes. Dark mode and colorblind-friendly mode are left for the next task.
- `src/components/ui/`, `src/hooks/use-mobile.ts`, `src/lib/utils.ts`:
  official shadcn components and supporting utilities. The mobile hook uses
  React's `useSyncExternalStore` to follow the browser breakpoint and comply
  with the existing React lint rules.
- `components.json`: shadcn configuration using Base UI, the neutral base
  color, CSS variables, and the existing `@/` import alias.

Use the header toggle to collapse the desktop sidebar or open the mobile drawer.
The sidebar also supports shadcn's Ctrl+B / Cmd+B shortcut. Selecting a mobile
navigation link closes the drawer.

## shadcn setup

Initialized with the official CLI (`shadcn@4.21.4` at implementation time):

```bash
npx shadcn@latest init --defaults --yes --no-monorepo
npx shadcn@latest add sidebar --yes
```

Only the sidebar and its required primitives were installed; no dashboard block
with example charts or analytics was added.

## Checks

```bash
npm run lint
npx tsc --noEmit
npm run build
```

## Previous Overview source

`reference/previous-overview.tsx.txt` is an unchanged copy of the previous page
with its year selector and FastAPI outage-summary request. It is preserved as
reference for future integration, outside the compiled application. The current
pages contain only titles and short placeholders as requested.
