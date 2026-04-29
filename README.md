# JobMatch

Local, personal-use job matching tool built with Python, SQLite, and NiceGUI.

## Run

```bash
python -m pip install -e .
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

To use a different port:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_jobmatch.ps1 -Port 8282
```

Because the project is installed with `-e`, code changes pulled from Git are used immediately. You only need to rerun setup when Python dependencies change, typically after edits to `pyproject.toml`.

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
