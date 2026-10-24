# Bataan Outage Dashboard

The existing frontend uses Next.js App Router, TypeScript, Tailwind CSS v4,
and npm. The shared shadcn/ui sidebar and layout wrap the dashboard pages.
Overview and Network Topology use the existing FastAPI server.

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
- `src/components/app-sidebar.tsx`: the Next.js links, active route
  highlighting, collapsed icon tooltips, and mobile drawer dismissal.
- `src/app/page.tsx`: Overview (`/`).
- `src/app/spatial/page.tsx`: Spatial & Event Analysis (`/spatial`).
- `src/app/network/page.tsx`: Network Topology (`/network`).
- `src/app/forecast/page.tsx`: Outage Forecasting (`/forecast`).
- `src/app/scenario/page.tsx`: Scenario Simulator (`/scenario`), marked planned.
- `src/app/globals.css`: neutral colors and semantic CSS variables for future
  themes, including dark appearance and the independent colorblind palette.
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

## Appearance and visualization colors

The header has Light, Dark, and System appearance options (default: System)
and an independent colorblind-friendly checkbox. `next-themes` manages the
`light`/`dark` class on `<html>` and persists `bataan-appearance` in localStorage.
`src/components/theme-provider.tsx` separately restores and persists
`bataan-colorblind`, reflected in `data-colorblind="true"` on `<html>`. Both
preferences survive navigation and reloads. Controls wait for hydration before
enabling, and inline initialization restores palettes before first paint.

`src/app/globals.css` is the source of truth for all colors:

- shadcn semantic variables style the dashboard in light and dark appearance.
- `--chart-1` through `--chart-5` define series colors.
- `--viz-background`, `--viz-text`, `--viz-grid` style future chart surfaces.
- `--map-background`, `--map-boundary`, `--map-low`, `--map-medium`,
  `--map-high`, and `--map-no-data` style future map layers.

The default visualization palette is neutral. Colorblind mode uses
[Okabe–Ito hues](https://jfly.uni-koeln.de/color/) adjusted for background
contrast. Navigation includes an underline and `aria-current` for the selected
route; the checkbox and explicit On/Off text communicate its state without
depending on color.

Future client-side ECharts/MapLibre components should import
`getVisualizationColors` from `@/lib/visualization-theme` and call it after
mounting. It returns resolved hex colors instead of CSS `var(...)` expressions,
which canvas and map color parsers cannot resolve themselves. Re-read colors
when `useTheme().resolvedTheme` or `useColorblindMode().colorblind` changes;
update the existing chart options or map paint properties rather than recreating
the visualization. For future charts and maps, always pair colors with clear
labels, different marker shapes, line dashes, or fill patterns, including a
distinct no-data label. No chart or map library is installed yet.

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
