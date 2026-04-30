# JobMatch

Local, personal-use job matching tool built with Python, SQLite, and NiceGUI.

## Current Direction

JobMatch is now strongest as:

- a local resume-aware matching engine
- a local cache of captured job opportunities
- a review dashboard for ranking, filtering, and exporting matches

Direct scraping still exists for API-backed and simpler boards, but protected sites are better handled through the browser capture extension in `browser_extension/`.

## Run

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m app.ui.main
```

The UI starts on `http://127.0.0.1:8181`.

## Windows Workflow

One-time setup on a new machine:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_jobmatch.ps1
```

If that machine already has the dependencies installed, you do not need to rerun setup for normal restarts.

Daily use after `git pull`:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_jobmatch.ps1
```

The launcher script runs from the repo folder automatically and will trigger setup on first launch if Python 3.12 does not have the required packages yet.
Setup logs are written to `data/logs/setup-*.log` so you can inspect the exact pip step if first-run installation is slow or fails.

To access the app from another machine on the same network, run it on the host machine and open the printed LAN URL in the other machine's browser.

To use a different port:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_jobmatch.ps1 -Port 8282
```

Because the app runs directly from the repo folder, code changes pulled from Git are used immediately. You only need to rerun setup when Python dependencies change, typically after edits to `requirements.txt` or `pyproject.toml`.

## CLI

```bash
python -m app.cli resume-import path/to/resume.pdf
python -m app.cli source-add --name "Acme Greenhouse" --url "https://boards.greenhouse.io/acme"
python -m app.cli scan
python -m app.cli matches
```

## Notes

- Resume uploads and cached jobs are stored locally in `data/`.
- Greenhouse and Lever use APIs first.
- Custom, Indeed-style, and clearance-style pages fall back to scraping.
- Dynamic pages can use Playwright when enabled on the source.

## Browser Capture Extension

The recommended ingestion path for LinkedIn, Indeed, and other dynamic pages is the bundled browser extension:

1. Load `browser_extension/` as an unpacked Chrome or Edge extension
2. Copy the browser capture token from `Settings` in JobMatch
3. In the extension popup, set:
   - the same JobMatch server URL you already use in the browser
   - the browser capture token
4. Open a jobs page and click `Capture visible jobs`

Captured jobs are stored in JobMatch as `browser_capture` sources. These sources are manual-only and are skipped by the scheduler.
