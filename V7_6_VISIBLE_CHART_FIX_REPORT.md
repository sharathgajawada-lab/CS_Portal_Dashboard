# v7.6 visible chart fix

Fixes:
- Dashboard charts no longer render as blank cards while only expanded charts work.
- Each canvas is now wrapped in a dedicated `.ux-canvas-stage`, so the toolbar does not steal Chart.js sizing context.
- Chart.js charts are resized after toolbar/stage injection and after async chart creation.
- Expanded Chart Studio no longer receives dashboard expand buttons.
- Session analytics now falls back more safely instead of returning early on failed session endpoints.
- Category loading now uses a bounded timeout and clear status instead of hanging indefinitely.
