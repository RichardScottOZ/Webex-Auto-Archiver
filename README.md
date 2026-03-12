# Webex-Auto-Archiver

This repository packages the upstream [`DJF3/Webex-Message-space-archiver`](https://github.com/DJF3/Webex-Message-space-archiver) archive script together with automation that can open a browser, discover your Webex personal access token, and reuse it to either generate download-everything scripts or run the archive for every room immediately.

## Included scripts

- `webex-space-archive.py` – the upstream Webex room archiver.
- `generate_space_batch.py` – lists your rooms and generates both `webex-space-archive-ALL.sh` and `webex-space-archive-ALL.bat`.
- `download_everything.py` – uses the same token-discovery flow and then runs the archive script for every room in sequence.
- `webex-auto-archiver.cmd` – Windows launcher for the `generate` and `download` workflows.

## Setup

1. Install the Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Install a Playwright browser for automatic token discovery:
   ```bash
   python -m playwright install chromium
   ```

If you already have a valid token, you can still provide it with `WEBEX_ARCHIVE_TOKEN` or `--token` and skip the browser automation entirely.

## Generate batch scripts for every Webex room

```bash
python generate_space_batch.py
```

What happens:
- a Chromium browser opens to the Webex developer token page;
- you complete login/MFA if required;
- the script watches the page for a valid personal access token and verifies it with the Webex API;
- once found, it fetches your spaces and writes both shell and Windows batch files.

Useful options:
- `--config-file path/to/webexspacearchive-config.ini`
- `--output-dir ./generated`
- `--match direct`
- `--skip-group` to keep only personal direct-message rooms
- `--skip-direct` to archive only group rooms
- `--limit 25`
- `--no-browser` if you are supplying the token some other way

## Archive everything immediately

```bash
python download_everything.py
```

This script uses the same token discovery flow, then launches `webex-space-archive.py` once for each room while passing the token through `WEBEX_ARCHIVE_TOKEN` in the child-process environment.

Use `--dry-run` to print the commands before executing them.

## Windows launcher

On Windows you can launch either workflow through the bundled `.cmd` wrapper:

```bat
webex-auto-archiver.cmd generate --skip-group --limit 10
webex-auto-archiver.cmd download --skip-group --match Richard --dry-run
```

## Notes

- Webex personal access tokens usually expire after 12 hours.
- If the token stays hidden on the Webex page, click the page's reveal/copy control once and the script will keep scanning for a valid token.
- `webex-space-archive.py` and its license come from Cisco's sample project; see `LICENSE` for the Cisco Sample Code License 1.1 text.

## Tests

Run the focused unit tests with:

```bash
python -m unittest discover -s tests
```
