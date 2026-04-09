<p align="center">
  <img src="branding/LibreseerrLogo.png" alt="Libreseerr" width="200">
</p>

# Libreseerr

Libreseerr is a book request management application for [Readarr](https://readarr.com/). It provides a simple web interface for users to search for books and request ebook or audiobook downloads similar to how [Overseerr](https://overseerr.dev/) and [Jellyseerr](https://github.com/Fallenbagel/jellyseerr) work for movies and TV shows with Sonarr/Radarr, but for books with Readarr.

![Libreseerr Discover Page](screenshots/discover.png)

## Features

- Search for books by title, author, or ISBN
- Request ebooks or audiobooks from separate Readarr instances
- Track download progress with real-time status updates
- View quality profiles and root folders from your Readarr server
- Manage and remove requests from a dedicated requests page

![Libreseerr Requests Page](screenshots/requests.png)

## Combatibility

- Confirmed combatible with [faustvii/readarr](https://github.com/Faustvii/Readarr/pkgs/container/readarr)

- Confirmed combatible with [penndydreadful/bookshelf](https://github.com/pennydreadful/bookshelf)

## Installation

### Docker Run

```bash
docker run -d \
  --name libreseerr \
  -p 5000:5000 \
  -v libreseerr-data:/app/data \
  --restart unless-stopped \
  ghcr.io/zamnzim/libreseerr:latest
```

### Docker Compose

```yaml
services:
  libreseerr:
    iimage: ghcr.io/zamnzim/libreseerr:latest
    ports:
      - "5000:5000"
    volumes:
      - libreseerr-data:/app/data
    restart: unless-stopped

volumes:
  libreseerr-data:
```

Start the stack with:

```bash
docker compose up -d
```

The application will be available at `http://<your-host>:5000`.

## Configuration

Open the web UI and navigate to the **Settings** page. From there you can configure your Readarr instances:

- **Ebook Server** — URL and API key for your Readarr instance serving ebooks
- **Audiobook Server** — URL and API key for your Readarr instance serving audiobooks

Click **Test Connection** to verify each server is reachable, then **Save**.

![Libreseerr Settings Page](screenshots/settings.png)

## Usage

1. Go to the **Discover** page and search for a book by title, author, or ISBN.
2. Click a book card to open the download dialog.
3. Select **ebook** or **audiobook**, choose a quality profile and root folder, then click **Download**.
4. Switch to the **Requests** page to monitor progress.
5. Click **Refresh Status** to poll Readarr for the latest download status.

Status indicators on the Requests page:

| Status | Meaning |
|---|---|
| **Processing** | Book has been sent to Readarr but is not yet in the download queue |
| **Downloading** | Book is actively downloading in Readarr (progress bar shown) |
| **Completed** | Download finished and file is available in Readarr |
| **Error** | Download failed (error message shown) |

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `PYTHONUNBUFFERED` | Ensures Python logs appear immediately in container output | `1` |

All application configuration (Readarr URLs and API keys) is managed through the web UI and stored in `/app/data/config.json`.

## Data Persistence

Application data is stored in `/app/data/` and includes:

- `config.json` — Readarr server configuration
- `requests.json` — Request history

Mount a volume at `/app/data` to persist this data across container restarts.

## Development

### Requirements

- Python 3.12+
- Flask, Requests, Gunicorn (see `requirements.txt`)

### Running Locally

```bash
pip install -r requirements.txt
python app.py
```

The development server starts on `http://0.0.0.0:5000` with debug mode enabled.

## License

See [LICENSE](LICENSE) for details.
