# Spatial selection performance

These changes apply to Spatial & Event Analysis. The Network Topology page keeps
its existing selection and camera behavior. Inputs and precomputed outage impacts
are unchanged.

## Selection lifecycle

The displayed outage remains available while the requested outage loads. Its
identifier is explicitly labeled as the previous outage. Details, source ripple
and highlights commit after data and the two feeder GeoJSON sources are ready.
Only those sources are awaited; internet basemap tiles never gate completion.
Failed requests keep the previous view. Abort signals and a map preparation token
prevent an older response from replacing a newer selection; canceled source
preparation restores the displayed feeder.

Repeated events on the same feeder reuse the graph, render sources and component
search array, even when camera movement is still loading tiles. New feeders update
the sources directly, without first replacing them with empty feature collections.
Theme labels/icons are regenerated for theme changes, not for each event.

Event details use a 24-entry shared request cache. Feeder graphs use a three-entry
LRU cache. Hover or keyboard focus lasting 150ms can preload an event and its
feeder, with one prefetch at a time. Canceling a viewer leaves a shared prefetch
available. Rejected cache entries can be retried.

`/api/impacts/events/{id}/view` serves compact compressed event data without
duplicated boundary geometry. `/api/impacts/boundaries?codes=...` supplies only
missing boundaries, cached in the browser by PSGC. Both support gzip and ETag
validation. The original event endpoint remains compatible. Concurrent initial
dataset requests share the same hash validation and Parquet load.

## Motion

Automatic fitting keeps the current camera if the new impact is already inside
the safe viewport margin, after the first selected event. Otherwise movement uses
800ms for nearby changes or 1100ms for larger changes, with smooth easing. Manual
Fit to impact still fits explicitly. Highlights fade over 200ms. Reduced-motion
users receive immediate updates. New selections stop obsolete camera movement.

## Verification and measurements

Developer builds emit `[outage-selection]` console records containing detail,
data, map-preparation and first-frame timings plus feeder-reuse status. User
Timing entries named `outage-selection:ready` are bounded. First-frame timings
exclude the intentional remainder of the camera animation.

The isolated backend sample in
`data/impacts/performance/spatial-20261025T213931.json` measured 3.14s for cold
dataset validation/loading and 2.29s for the first feeder payload build. Cached
calls were below the recorded timer precision. The sample's old event JSON was
33,533 bytes; compact JSON was 11,408 bytes, or 4,882 bytes with gzip. Boundaries
are now loaded separately on first use, so this is not the total initial transfer.

Initial browser testing recorded a cold selection at 15.6s under development
load, mostly in the data stage; warm same-feeder samples reached their first
frame in 93–182ms, with 7–10ms map preparation. These are local diagnostic samples,
not guaranteed latency or a controlled before/after benchmark. Initial uncached
geometry downloads and development compilation can still take longer.

Validation: Node cache/cancellation and source-anchor tests; backend compact-view,
compression, boundary, conditional-request and impact-consistency tests;
TypeScript and ESLint; browser checks for transitions and rapid selections.
