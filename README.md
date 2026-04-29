# JobMatch

Local, personal-use job matching tool built with Python, SQLite, and NiceGUI.

## Run

```bash
python -m pip install -e .
python -m playwright install chromium
python -m app.ui.main
```

The UI starts on `http://127.0.0.1:8080`.

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
