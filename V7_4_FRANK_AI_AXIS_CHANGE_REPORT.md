# v7.4 Frank AI + chart axis visibility fix

## Product/data fixes
- Frank AI consultants are now treated as Voice AI team consultants.
- Any consultant name or ID containing `Frank AI`, `Frank-AI`, or `FrankAI` is mapped into the `Voice AI` team before aggregation.
- Frank AI consultants remain visible in consultant dropdowns and consultant tables.
- Voice AI remains a normal team dropdown option; the removed Voice AI lens/Domo team pill are not restored.

## Chart/UI fixes
- Chart containers now have enough height for labels and toolbars.
- Chart.js layout padding reserves bottom space for x-axis tick labels.
- X-axis ticks are forced visible with auto-skip and controlled rotation.
- The clean toolbar stays intact and every chart keeps expand/export/inspect actions.

## Deploy note
Replace the root files, then use Render: Manual Deploy -> Clear build cache & deploy. The service-worker cache was bumped to `cs-portal-v10-frank-ai-axis-visible`.
