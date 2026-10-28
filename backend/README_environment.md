# Environmental profile integration

Spatial Analysis Row 1 reads the completed 2025 environmental catalog. The image route is part of the existing `environment_context` router; no additional router registration is required.

## Routes

- `GET /api/spatial/environment`: Bataan profile with 12 municipality image descriptors.
- `GET /api/spatial/environment?municipality_psgc=0300808000`: municipality profile.
- `GET /api/spatial/environment?barangay_psgc=0300808001`: barangay profile. If both PSGCs are provided, their parent relationship must match.
- `GET /api/spatial/environment/rasters/2025/{municipalities|barangays}/{psgc}/{ndvi|slope|combined}`: precomputed lossless WebP, checksum ETag, conditional 304 and a one-hour revalidation cache policy.

Metadata includes per-layer image URLs, recorded TL/TR/BR/BL corners, bounds, dimensions, status, availability, source-basis text and coverage. URLs are relative to the backend origin. Only geography changes the environmental profile. Outage year and cause continue to affect the existing outage features independently; environmental NDVI remains 2025 and terrain remains static.

All statistics come from per-area metadata in `data/environment_rasters/2025`. Province means use native valid-pixel weights across the 12 municipalities; extrema are min/max of their actual extrema. Joint percentages divide the summed class count by summed jointly valid 30 m cells. A focused test verifies municipality native-grid pixel counts partition the province without duplication. No parent/child mixing, averaging percentages, raster reading, or new slope calculations occur during requests.

The existing loader caches JSON by mtime/size. Summary cache keys include catalog and selected-metadata revisions. Image requests resolve only allowlisted geographic levels/types and string PSGCs, and never accept a caller-provided path.

## Frontend

`environmental-raster-map.tsx` owns one independent MapLibre instance. Only the active raster type is requested: 12 overlays for province mode, one for municipality/barangay. Aborted selection requests never commit; all images are decoded before replacing the visible overlay group. The compressed-image LRU holds at most 24 completed images. Decoded bitmap ownership follows map sources and is released on replacement/unmount.

The viewer uses recorded Web Mercator image corners, nearest-neighbor raster rendering, full opacity and unchanged source colors. It has keyboard pan/zoom, north-up controls, a scale bar and Fit area. Geography changes fit the extent; changing the layer preserves camera position. Loading/error covers prevent stale geography from being presented as the new selection. An explicit full-height inner container prevents MapLibre's relative-position CSS from collapsing the map.

The approved continuous and nine-cell legends remain in the existing profile. Coverage reports refer to measured analysis cells, while transparent polygon-edge pixels can also result from masking at two resolutions. Wawa, Limay has 80.85% jointly valid aligned coverage; missing cells remain transparent. Native NDVI statistics use 10 m measurements, while the displayed NDVI and joint classes use aligned 30 m cells, as documented in the profile.

The existing causes donut, click-to-filter callback, event table, outage-impact map, Weather Context, Overview and topology behavior were not refactored.

## Focused verification

- `.\.venv\Scripts\python.exe -m unittest backend.tests.test_environment_context -v`: 8 passing real-ASGI tests, including image bytes/ETags, zero extrema, missing data, parent consistency, province partitioning and metadata revision changes. Uses the existing stack without adding the optional httpx2 package.
- From `frontend`: `node --test tests/environment.test.mjs`: 2 passing image-cache/abort/retry tests.
- From `frontend`: `node_modules\.bin\tsc.cmd --noEmit` and targeted ESLint for the three environmental TS/TSX files: passed.
- `next build --webpack`: passed, including production TypeScript and static routes. Default Turbopack build encountered a CSS-worker startup failure; build configuration was left unchanged.
- Short browser checks: Bataan, Morong, Binaritan, Wawa/Limay, all three raster buttons, quick layer switches, outage-year independence, narrow stacked layout and desktop three-column layout. Confirmed actual raster rendering after repairing the container sizing issue.
- Screenshot: `data/spatial_previews/environment_integration/morong-viewer.png`.

Source GeoTIFFs, generated WebPs, the approved sample and the raster manifest were not regenerated or edited by this integration.
