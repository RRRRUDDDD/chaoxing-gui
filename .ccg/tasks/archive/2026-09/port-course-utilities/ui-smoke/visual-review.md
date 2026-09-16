# Course tool UI smoke

Final run: 2026-09-16, Chrome headless with Playwright, built `web/dist` served from an isolated loopback fixture server.

- Viewports: desktop 1366×900 and mobile 390×844.
- Each viewport exercised fixture login, explicit course selection, invalid/valid visits configuration and results, video catalog selection and duration validation, source expiry error and retry, video progress and stop, download search/type filtering with retained selections, partial download results, and folder-open failure/retry.
- No real platform access, account data, file downloads, or file-manager launch. All API responses are fixtures; external browser requests are blocked.
- 34 fixture API calls per viewport. Both flows passed. No page errors, attempted external requests, horizontal document overflow, or controls outside the viewport.
- Inspected screenshots for configuration, results, catalog selection, active video progress, source expiry, and folder-open errors. Long filenames and paths wrap at 390px; labels and action buttons remain legible.
- Fixed one visual/flow issue: follow-up start errors previously appeared above the resource list, outside the mobile user's current view. They now render beside the resource start button. The regression test and smoke assert that placement.
- Full-page captures reset document scroll first so sticky headers are recorded at the top.
- Browser contexts, browser, and fixture server closed in `finally`; `report.json` records cleanup success.

Validation after the fix: 62 component/regression tests passed; production build passed; both smoke viewports passed. Existing browser-data age warnings are non-blocking.

The 18 final screenshots are listed individually in `report.json`. Recommended review images: `desktop-video-start-error.png`, `mobile-video-start-error.png`, `mobile-download-catalog.png`, `mobile-download-open-error.png`, and `mobile-visits-results.png`.
