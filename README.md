# Vinted-Notifications

A real-time notification system for Vinted listings that works across all Vinted country domains. Get instant alerts
when items matching your search criteria are posted.

![Vinted-Notifications](https://github.com/user-attachments/assets/f2788511-5a8a-4a8d-8198-a4135081a3d8)

---

## ⚡ Quickstart

If you just want to get started fast with Docker Compose:

```bash
curl -O https://raw.githubusercontent.com/Fuyucch1/Vinted-Notifications/main/docker-compose.yml
docker-compose up -d
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

---

## 📋 Features

- **Web UI**: Manage everything through an intuitive web interface
- **Multi-Country Support**: Works on all Vinted domains regardless of country
- **Real-Time Notifications**: Get instant alerts for new listings
- **Multiple Search Queries**: Monitor multiple search terms simultaneously
- **Country Filtering**: Filter items by seller's country of origin
- **RSS Feed**: Subscribe to your search results with any RSS reader
- **Telegram Integration**: Receive notifications directly in Telegram, with photos, buy buttons, inline query management,
  quiet hours and diagnostics
- **Browser Session (Patchright)**: The Vinted cookies are obtained with a real Chromium browser and reused for the API
  calls, which makes the scraping much more reliable
- **Reliable delivery**: notifications are stored in the database and retried, so a restart never loses an item

---

## 📦 Installation

### Option 1: Docker Run (Simplest)

#### Prerequisites

- Docker installed on your system
- Telegram bot token (for Telegram notifications)

#### Setup with Docker Run

1. **Create directories for persistent data**

   ```bash
   mkdir -p data logs
   ```

2. **Run the container**

   ```bash
   docker run -d \
     --name vinted-notifications \
     -p 8000:8000 \
     -p 8080:8080 \
     -v "$(pwd)/data:/app/data" \
     -v "$(pwd)/logs:/app/logs" \
     --restart unless-stopped \
     fuyucch1/vinted-notifications:latest
   ```

   > **Note**: The volume mounts ensure your data and logs are preserved even if the container is removed or updated.
   The database is stored in the `data` directory, and logs are stored in the `logs` directory.

3. **Access the Web UI**

   Once started, access the Web UI at [http://localhost:8000](http://localhost:8000) to complete the setup.

### Option 2: Docker Compose (Recommended)

#### Prerequisites

- Docker and Docker Compose installed on your system
- Telegram bot token (for Telegram notifications)

#### Setup with Docker Compose

1. **Create a docker-compose.yml file**

   ```yaml
   version: '3.8'

   services:
     vinted-notifications:
       image: fuyucch1/vinted-notifications:latest
       pull_policy: always
       ports:
         - "8000:8000"
         - "8080:8080"
       volumes:
         - VN_data:/app/data
         - VN_logs:/app/logs
       restart: unless-stopped
       read_only: true
       tmpfs:
         - /tmp

   volumes:
     VN_data:
     VN_logs:
   ```

   > **Note**: This configuration uses named volumes (`VN_data` and `VN_logs`) which are managed by Docker. This ensures
   your data and logs are preserved even if the container is removed or updated.

2. **Start the container**

   ```bash
   docker compose up -d
   ```

   To build the image from a local checkout (instead of using the published one) keep the provided
   `docker-compose.override.yml` next to `docker-compose.yml` and run:

   ```bash
   VN_WEB_PORT=8010 docker compose up -d --build
   ```

   `VN_WEB_PORT` / `VN_RSS_PORT` change only the *host* ports, which is handy when 8000 or 8080 are already in use.

3. **Access the Web UI**

   Once started, access the Web UI at [http://localhost:8000](http://localhost:8000) to complete the setup.

### Option 3: Self-Build

#### Prerequisites

- Python 3.11 or higher
- Telegram bot token (for Telegram notifications)

#### Setup

1. **Clone the repository or download the latest release**

   ```bash
   git clone https://github.com/Fuyucch1/Vinted-Notifications.git
   cd Vinted-Notifications
   ```

   Alternatively, download the [latest release](https://github.com/Fuyucch1/Vinted-Notifications/releases/latest) and
   extract it.

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Initial Configuration**

   The application can be configured through the Web UI after starting. The port of the Web UI itself (8000 by default)
   is the `Web UI Port` field of the Configuration page, next to the other system settings.

4. **Run the application**

   ```bash
   python vinted_notifications.py
   ```

   Once started, access the Web UI at [http://localhost:8000](http://localhost:8000) to complete the setup.

## 🚀 Usage

### Web UI

The Web UI is the easiest way to manage the application. Access it at [http://localhost:8000](http://localhost:8000)
after starting the application.

Features available in the Web UI:

- **Dashboard**: Overview of application status and recent items
- **Queries Management**: Add, remove, and view search queries
- **Items Viewing**: Browse and filter items found by the application
- **Allowlist Management**: Filter items by seller's country
- **Configuration**: Set up Telegram bot, RSS feed, and other settings
- **Logs**: View application logs directly from the web interface

### Telegram Commands

After configuring your Telegram bot in the Web UI, you can use the following commands:

- `/start`, `/help` - Show the available commands
- `/add_query [name=]query` - Add a search query to monitor
- `/remove_query query_number` - Remove a specific query
- `/remove_query all` - Remove all queries
- `/queries` - List the queries with inline buttons to pause or delete them
- `/test url` - Run a search once and show the results without saving or notifying anything
- `/hello` - Check if the bot is working
- `/add_country XX` - Add a country to the allowlist (ISO3166 format)
- `/remove_country XX` - Remove a country from the allowlist
- `/clear_allowlist` - Allow every country again
- `/allowlist` - View the current allowlist
- `/banwords`, `/add_banword word`, `/remove_banword word` - Manage the excluded words
- `/pause`, `/resume` - Stop and resume the notifications (new items stay queued)
- `/quiet HH:MM-HH:MM` - Queue the notifications during a time window (`/quiet off` to disable it)
- `/status` - Browser session, pending notifications and last item found
- `/stats` - Items found per query and per day
- `/refresh_session` - Force the Vinted browser session to be renewed

### Query Examples

Queries must be added with a whole link. It works with filters.:

   ```
   /add_query https://www.vinted.fr/catalog?search_text=nike%20shoes&price_to=50&currency=EUR&brand_id[]=53
   ```

### RSS Feed

The RSS feed provides an alternative way to receive notifications. After enabling it in the Web UI, access it
at [http://localhost:8080](http://localhost:8080).

## ⚙️ Advanced Configuration

### Browser Session (Patchright)

The cookies of Vinted are obtained with
[Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python), a patched Chromium driver. The browser is only
used by the scraper process; the resulting cookies, User-Agent and proxy are shared with the other processes through the
database.

- **essential** mode (default): the browser only produces the cookies, the API calls stay plain HTTP requests.
- **full** mode: the API calls are performed from inside the browser context, which is slower but harder to block. It is
  also used automatically as a fallback when the plain requests are rejected.

The session is renewed every `browser_ttl` seconds and whenever a 401/403/429 is received, from the Telegram command
`/refresh_session` or from the Configuration page.

When self-building you have to download the browser once:

```bash
pip install -r requirements.txt
patchright install chromium   # add --with-deps on a bare Linux host
```

The Docker image already contains it. Notes for containers:

- The browser needs a writable profile directory (`./data/browser_profile` by default) and a writable `HOME`.
- The sandbox is disabled by default (`browser_no_sandbox`), because it does not work in most container runtime
  configurations: this lowers the isolation of the browser process, keep it in mind.
- `headless=False` is more stealthy but requires an X11 server (e.g. `xvfb-run`).

### Proxy Support

The application supports using proxies to avoid rate limits. Those are configured in the configuration tab of the Web
UI.

### Custom Notification Format

You can customize the notification message format:

```python
MESSAGE = '''\
🆕 Title: {title}
💶 Price: {price}
🛍️ Brand: {brand}
<a href='{image}'>&#8205;</a>
'''
```

## 🔄 Updating

### Option 1: Docker Run

1. Pull the latest image:
   ```bash
   docker pull fuyucch1/vinted-notifications:latest
   ```
2. Stop and remove the existing container:
   ```bash
   docker stop vinted-notifications
   docker rm vinted-notifications
   ```
3. Run the container again with the same command as in the installation section:
   ```bash
   docker run -d \
     --name vinted-notifications \
     -p 8000:8000 \
     -p 8080:8080 \
     -v "$(pwd)/data:/app/data" \
     -v "$(pwd)/logs:/app/logs" \
     --restart unless-stopped \
     fuyucch1/vinted-notifications:latest
   ```

   > **Important**: This update method preserves your data because of the volume mounts. Your database and logs will be
   maintained across updates as they are stored in the mounted directories.

### Option 2: Docker Compose

1. Pull the latest image:
   ```bash
   docker pull fuyucch1/vinted-notifications:latest
   ```
2. Restart your container:
   ```bash
   docker-compose down
   docker-compose up -d
   ```

   > **Important**: This update method preserves your data because of the named volumes (`VN_data` and `VN_logs`). Your
   database and logs will be maintained across updates as they are stored in these Docker-managed volumes.

### Option 3: Self-Build

1. Download the latest [release](https://github.com/Fuyucch1/Vinted-Notifications/releases/latest)
2. Back up your `vinted_notifications.db` file
3. Replace all files with the new ones
4. Restart the application

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📜 License

This project is licensed under the [GNU AFFERO GENERAL PUBLIC LICENSE](LICENSE).

## 🙏 Acknowledgements

- Thanks to [@herissondev](https://github.com/herissondev) for maintaining pyVinted, a core dependency of this project.
