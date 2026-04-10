<p align="center">
  <img src="branding/LibreseerrLogo.png" alt="Libreseerr" width="200">
</p>

# Libreseerr

Libreseerr is a book request management application for [Readarr](https://readarr.com/) and Readarr forks like [Bookshelf](https://github.com/pennydreadful/bookshelf). It provides a simple web interface for users to search for books and request ebook or audiobook downloads similar to how [Seerr](https://github.com/seerr-team/seerr) works for movies and TV shows with Sonarr/Radarr, but for books with Readarr or Readarr Forks.

![Libreseerr Discover Page](screenshots/discover.png)

## Features

- Search for books by title, author, or ISBN
- Request ebooks or audiobooks from separate Readarr or Bookshelf instances
- Track download progress with real-time status updates
- View quality profiles and root folders from your Readarr or Bookshelf server
- Manage and remove requests from a dedicated requests page
- User authentication with session management
- Admin-only user management (create, edit, delete users)

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

On first launch, a default admin account is created with the following credentials:

| Username | Password |
|---|---|
| `admin` | `admin` |

**Change the default password immediately** from the **Users** page after logging in.

Log in with these credentials to access the web UI. The **Settings** page (admin only) lets you configure your Readarr instances:

- **Ebook Server** - URL and API key for your Readarr instance serving ebooks
- **Audiobook Server** - URL and API key for your Readarr instance serving audiobooks

Click **Test Connection** to verify each server is reachable, then **Save**.

![Libreseerr Settings Page](screenshots/settings.png)

## Usage

1. Log in with your admin credentials (default: `admin` / `admin`).
2. Go to the **Discover** page and search for a book by title, author, or ISBN.
3. Click a book card to open the download dialog.
4. Select **ebook** or **audiobook**, choose a quality profile and root folder, then click **Download**.
5. Switch to the **Requests** page to monitor progress.
6. Click **Refresh Status** to poll Readarr for the latest download status.

Status indicators on the Requests page:

| Status | Meaning |
|---|---|
| **Processing** | Book has been sent to Readarr but is not yet in the download queue |
| **Downloading** | Book is actively downloading in Readarr (progress bar shown) |
| **Completed** | Download finished and file is available in Readarr |
| **Error** | Download failed (error message shown) |

## User Management

On first launch, a default admin account is created:

- **Username:** `admin`
- **Password:** `admin`

**Change the default password immediately** after your first login.

Admins can manage users from the **Users** page:

- **Add User** - Create new accounts with a username, password, and role (`admin` or `user`)
- **Edit User** - Update a user's password or role
- **Delete User** - Remove a user account (you cannot delete your own account)

Non-admin users can search for books and make requests but do not have access to the **Settings** or **Users** pages.

LDAP Users are also supported via Users > Advanced.

![Libreseerr Users Page](screenshots/users.png)

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `PYTHONUNBUFFERED` | Ensures Python logs appear immediately in container output | `1` |
| `SECRET_KEY` | Flask session secret key. Set this to a stable value in production | Auto-generated on first run |

All application configuration (Readarr URLs and API keys) is managed through the web UI and stored in `/app/data/config.json`.

## Data Persistence

Application data is stored in `/app/data/` and includes:

- `config.json` - Readarr server configuration
- `requests.json` - Request history
- `users.json` - User accounts (passwords are hashed)
- `secret_key` - Auto-generated session signing key (only created if `SECRET_KEY` is not set via environment)

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
