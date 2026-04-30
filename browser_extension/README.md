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
6. Choose how many result pages to capture in the popup
7. Open a jobs page, then click `Capture visible jobs`

On supported search pages, the extension now clicks through the visible result list and captures each visible detail pane before sending the batch to JobMatch. That is slower than a shallow list scrape, but it gives much better salary and requirement text.

When you request more than one page, the extension follows supported next-page links and appends those jobs into the same import batch. The current pass supports paginated search capture on:

- Indeed
- ClearanceJobs

## Supported first-pass page types

- LinkedIn company jobs pages
- Indeed search pages
- generic company/ATS pages that expose visible job cards or JSON-LD job postings

This extension is meant to feed the local JobMatch app. Captured sources are stored as `browser_capture` sources and are skipped by the scheduler.
