<p align="center">
  <img src="branding/LibreseerrLogo.png" alt="Libreseerr" width="200">
</p>

# Libreseerr

Libreseerr is a book request management application for [Readarr](https://readarr.com/), Readarr forks like [Bookshelf](https://github.com/pennydreadful/bookshelf), and [LazyLibrarian](https://gitlab.com/LazyLibrarian/LazyLibrarian). It provides a simple web interface for users to search for books and request ebook or audiobook downloads similar to how [Seerr](https://github.com/seerr-team/seerr) works for movies and TV shows with Sonarr/Radarr, but for books with Readarr, Readarr forks, or LazyLibrarian.

![Libreseerr Discover Page](screenshots/discover.png)

## Features

- Search for books by title, author, or ISBN
- Request ebooks or audiobooks from separate Readarr, Bookshelf, or LazyLibrarian instances
- Track download progress with real-time status updates
- View quality profiles and root folders from your configured server
- Manage and remove requests from a dedicated requests page
- User authentication with session management
- Admin-only user management (create, edit, delete users)

![Libreseerr Requests Page](screenshots/requests.png)

## Compatibility

- Confirmed compatible with [faustvii/readarr](https://github.com/Faustvii/Readarr/pkgs/container/readarr)

- Confirmed compatible with [penndydreadful/bookshelf](https://github.com/pennydreadful/bookshelf)

- Confirmed compatible with [LazyLibrarian](https://gitlab.com/LazyLibrarian/LazyLibrarian)

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
    image: ghcr.io/zamnzim/libreseerr:latest
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

### TrueNAS Scale

Libreseerr is available as a TrueNAS SCALE app from this repository.

1. In the TrueNAS web UI, go to **Apps** > **Settings** > **Add Catalog**.
2. Enter the repository URL: `https://github.com/zamnzim/Libreseerr`
3. Set the **Preferred Train** to `stable`.
4. Navigate to **Available Applications**, find **Libreseerr**, and click **Install**.
5. Configure the app through the UI:
   - **Libreseerr Configuration** — image settings, secret key, and timezone
   - **Storage** — choose `ixVolume` (managed) or `hostPath` (custom directory) for persistent data
   - **Networking** — set the web port (default 50000)
6. Click **Install** and wait for the app to deploy.

The app will be accessible at `http://<your-truenas-ip>:50000`.

Note: TrueNAS does not auto-pull new latest images and users need to update manually via **Apps** > **Update**.

## Configuration

On first launch, a default admin account is created with the following credentials:

| Username | Password |
|---|---|
| `admin` | `admin` |

**Change the default password immediately** from the **Users** page after logging in.

Log in with these credentials to access the web UI. The **Settings** page (admin only) lets you configure your server instances:

- **Ebook Server** - URL and API key for your Readarr, Bookshelf, or LazyLibrarian instance serving ebooks
- **Audiobook Server** - URL and API key for your Readarr, Bookshelf, or LazyLibrarian instance serving audiobooks

Select the **Server Software** dropdown to choose between Readarr, Bookshelf, or LazyLibrarian for each server type.

Click **Test Connection** to verify each server is reachable, then **Save**.

![Libreseerr Settings Page](screenshots/settings.png)

## Usage

1. Log in with your admin credentials (default: `admin` / `admin`).
2. Go to the **Discover** page and search for a book by title, author, or ISBN.
3. Click a book card to open the download dialog.
4. Select **ebook** or **audiobook**, choose a quality profile and root folder, then click **Download**.
5. Switch to the **Requests** page to monitor progress.
6. Click **Refresh Status** to poll your server for the latest download status.

Status indicators on the Requests page:

| Status | Meaning |
|---|---|
| **Queued** | Request has been accepted and is waiting for the background worker to submit it |
| **Processing** | Book has been sent to the server but is not yet in the download queue |
| **Downloading** | Book is actively downloading (progress bar shown) |
| **Completed** | Download finished and file is available in the server |
| **Error** | Download failed (error message shown) |

## Async Request Processing

Libreseerr queues requests immediately and processes the backend Readarr,
Bookshelf, or LazyLibrarian call in the background.

When a user clicks **Download**:

1. The request is saved immediately with status `pending`.
2. The browser receives a `202 Accepted` response right away.
3. A background worker sends the book to the configured backend.
4. The request status changes to `processing`, `downloading`, `completed`, or `error`.

This avoids browser timeouts when Bookshelf or metadata providers are slow.

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

OIDC (OpenID Connect) sign-in is also supported via Users > Advanced. When enabled, the
login page shows a "Sign in with [provider]" button alongside the username/password form.
Configure with your IdP's issuer URL, client ID, and client secret. Optional toggles:
- **Auto-create users on first login**: provision new users on successful OIDC sign-in (off by default)
- **Auto-redirect to OIDC on login page**: skip the username/password form and go straight to the IdP. If your IdP later becomes unreachable, append `?bypass=1` to the login URL (`/login?bypass=1`) to surface the local form again without editing config on disk.

The redirect URI to register at your IdP is `https://<your-libreseerr-host>/api/auth/oidc/callback`.

![Libreseerr Users Page](screenshots/users.png)

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `PYTHONUNBUFFERED` | Ensures Python logs appear immediately in container output | `1` |
| `SECRET_KEY` | Flask session secret key. Set this to a stable value in production | Auto-generated on first run |
| `BOOKSHELF_LOOKUP_TIMEOUT` | Timeout in seconds for Bookshelf `/book/lookup` calls | `20` |
| `BOOKSHELF_AUTHOR_LOOKUP_TIMEOUT` | Timeout in seconds for Bookshelf `/author/lookup` calls | `20` |
| `BOOKSHELF_API_TIMEOUT` | Timeout in seconds for Bookshelf add/update API calls | `30` |
| `REQUEST_CLAIM_TTL_SECONDS` | How long a queued request lease stays valid before recovery can reclaim it | `120` |
| `REQUEST_RECOVERY_SCAN_INTERVAL_SECONDS` | Minimum interval between background recovery scans | `30` |
| `REQUEST_RECOVERY_BATCH_SIZE` | Maximum number of pending requests reclaimed per recovery scan | `10` |

All application configuration (server URLs, API keys, and server software type) is managed through the web UI and stored in `/app/data/config.json`.

## Data Persistence

Application data is stored in `/app/data/` and includes:

- `config.json` - Server configuration (Readarr, Bookshelf, or LazyLibrarian)
- `requests.json` - Request history
- `users.json` - User accounts (passwords are hashed)
- `secret_key` - Auto-generated session signing key (only created if `SECRET_KEY` is not set via environment)

Mount a volume at `/app/data` to persist this data across container restarts.

## Fork Image Publishing

This fork can publish its own image to GitHub Container Registry with the included
workflow in `.github/workflows/docker-publish.yml`.

The published image name is:

```text
ghcr.io/<your-github-owner>/libreseerr:latest
```

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
