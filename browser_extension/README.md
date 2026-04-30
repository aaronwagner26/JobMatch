# JobMatch Capture Extension

Load this folder as an unpacked Chrome or Edge extension.

## Use

1. Open `chrome://extensions` or `edge://extensions`
2. Enable developer mode
3. Choose `Load unpacked`
4. Select the `browser_extension/` folder
5. Open the JobMatch popup and paste:
   - the same JobMatch server URL you already use in the browser
   - the browser capture token from JobMatch Settings
6. Open a jobs page, then click `Capture visible jobs`

## Supported first-pass page types

- LinkedIn company jobs pages
- Indeed search pages
- generic company/ATS pages that expose visible job cards or JSON-LD job postings

This extension is meant to feed the local JobMatch app. Captured sources are stored as `browser_capture` sources and are skipped by the scheduler.
