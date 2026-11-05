# Changelog

All notable changes to Cabcrawl (called Arcade Tracker until October 2026) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **ROM images from GATBOX** (contract v1, capability `roms`, planned in the integration plan's phase C).
  A machine's chip dumps and burns now arrive here: the image first, by hash and in parts that
  resume, then a `rom_dump` or `burn` record. The machine page and the work order it was made for list
  them; an operator or above can download the image. A dump or burn is accepted only when its image is
  stored and its size, SHA-1 and CRC32 match the stored bytes, and for a device limited to some
  machines, only those. Images are kept in `uploads/rom_files/` (already writable under the service and
  already in the uploads backup). The migration adds three tables (`rom_file`, `rom_dump`, `rom_burn`).
  `/api/v1/health` now says `app` and `capabilities`. A history reset keeps the dumps and burns and
  clears their link to the deleted work orders. The deck sends nothing until it has the matching change.

## [3.0.0] - 2026-10-27

The biggest release since 1.0: a new name, a new design, a full inventory system, the GATBOX
link, and two security reviews. Printed QR labels, the database and the GATBOX contract all
carry over: an existing install upgrades with `deploy/deploy.sh`, which backs up the database
and runs the migrations.

### Changed
- **The app is now called Cabcrawl** (it was Arcade Tracker). The header wordmark reads CABCRAWL,
  the browser tab and home-screen icon say Cabcrawl, and so does the footer credit when an arcade
  sets its own name or logo. GATBOX tests the board; Cabcrawl runs the floor. The repository is
  `digitalunconciousness/cabcrawl`.
- Nothing a machine, a label or a server depends on changed: every URL (including `/g/<barcode>`
  on printed QR labels), the GATBOX API contract, the database, role, cookie and environment
  variable names, the systemd unit `arcade-tracker.service` and the server paths keep their
  names. `deploy/RUNBOOK.md` says so.
- **A new design**, phone first, in GATBOX's colours: one stylesheet set and one base template,
  every component on `/styleguide`, no CDN assets and a strict content security policy. Every
  page was rebuilt on it and the old templates were deleted.
- **Plays come first**, token value second, so nobody mistakes token value for revenue. Machines
  are ranked by how often they're played.
- **Production runs on PostgreSQL** under systemd behind a Cloudflare tunnel, deployed by
  `deploy/deploy.sh`, which backs up the database before every deploy.
- The README is rewritten, the user guide is the wiki (its source is `docs/wiki/`), and the old
  root documents and scripts moved to `docs/history/`.

### Added
- **Machines**: a page per cabinet with three big buttons (record plays, report problem, log
  work), how it was fixed last time, and every location and status change with dates. Camera
  scanning on the Scan page. Whole-floor label sheets.
- **Coin-door reports**: a label inside the coin door lets anyone report a problem without
  signing in.
- **Collection round**: walk the floor and read every meter, with odd readings flagged on the
  spot.
- **Rotation planner**: ranks floor games by plays per working day, leaves out days a game was
  down, and suggests swaps with the warehouse.
- **Maintenance tasks** that open work orders when they come due, reserving the parts they need.
- **Inventory**:
  - places (rooms, shelves, drawers) and how many of each part are in each;
  - labels for places and parts, and count mode;
  - full and cycle counts, reviewed by a manager;
  - part categories, suppliers and price history;
  - a shopping list, and purchase orders;
  - serial-numbered units;
  - parts reserved for work orders.
- **GATBOX**: each device has its own token and can be limited to some machines. Rail sessions
  are stored and drawn against their window, and a reading can be attached to a work order.
- **Alerts** to ntfy and Discord: coin-door reports, machines down, rails out of window, low
  stock, maintenance due, collection-round flags, part requests, and a daily summary. They're
  held outside opening hours.
- **Branding**: the arcade's own name, logo and sign-in background.
- **The AGPL-3.0**, with a link to the source in every page's footer.
- **Docker**: `docker-compose.yml` runs Cabcrawl with PostgreSQL 17 and an alerts container.
  The image runs as a non-root user with waitress, creates the database on first start and
  migrates it on later ones.

### Security
- CSRF on every form. A strict content security policy. Secure cookies through the tunnel. Login
  rate limits by the visitor's real address. One-time passwords for new and reset accounts.
- Two full security reviews (`docs/FEATURES.md`, F-43 and F-44). Every finding is fixed, each
  with a test.

### Fixed
- Dozens of long-standing bugs, each recorded in `docs/FEATURES.md` with its F-number and
  fixed with a test. Among them:
  - deleting a game or a part left orphaned rows;
  - "Received" requests could add stock twice;
  - photo cleanup deleted live photos;
  - the first-run setup page returned an error;
  - forms were refused through the tunnel.

## [2.0.0] - 2026-03-25

### Changed
- A major refactor for readability: the `app/` package (an application factory with routes,
  models and forms) beside the old `app.py`, every dependency updated, and the Raspberry Pi
  hardware packages for skeeball added.

## [1.0.1] - 2025-11-30

### Fixed
- Tab buttons work again.

## [1.0.0] - 2025-11-13

### 🎉 Version 1.0 Release

This is the first major release of Arcade Tracker, featuring a complete arcade management system with real-time skeeball integration, automated backups, and comprehensive inventory and maintenance tracking.

### Added

#### Core Features
- Complete game inventory management with location tracking (Floor, Warehouse, Shipped)
- Revenue and play tracking with automatic calculations from coin counters
- Advanced maintenance system with timestamped work logs and audit trails
- Inventory management with parts/supplies tracking and low stock alerts
- Role-based access control system (readonly, operator, manager, admin)
- Professional PDF report generation with ReportLab
- CSV data export functionality
- Database backup and restore with Fernet encryption
- **Automated daily backups** via cron job (runs at 2 AM, cleans up backups >30 days old)
- Monthly performance tracking (top 5/top 10 rankings)
- Executive summary dashboard with actionable insights

#### Skeeball Integration
- Complete Raspberry Pi-based skeeball scoring system
- Automatic scoring with 7-sensor configuration (coin, ball counter, 5 scoring holes)
- **Real-time game state updates** with 0.5-second control panel refresh
- **Statistics page** with 1-second updates showing cumulative totals and best scores
- File-based state synchronization avoiding GPIO conflicts
- Stats API server (port 5002) serving real-time data
- Web-based control panel for lane management
- Simulator mode for testing without hardware
- GPIO testing interface for hardware diagnostics
- Multi-lane architecture support (single Pi 4 + Pico per lane)
- Persistent game state surviving power loss
- Bonus game system with configurable thresholds
- Revenue reconciliation with automatic daily sync

#### Real-Time Skeeball Features (v1.0)
- State saves on coin insert, switch trigger, and ball completion
- Control panel shows live score, balls remaining, and game active status
- Statistics display total games, total coins, and best score
- No page refresh needed - updates appear as you play
- Background `state_sync.py` process watches state file and syncs every second
- Stats API loads game history from source file for accurate statistics

#### User Interface
- Cyberpunk-themed responsive design
- Clean dashboard with revenue overview and inventory alerts
- **Low stock alerts** displayed on home dashboard
- Professional maintenance reports with work timeline visualization
- User management interface without unnecessary email column
- Profile management with picture uploads
- Simplified skeeball statistics (focused on essential metrics)

#### Security
- Flask-Login session-based authentication
- CSRF protection on all forms via Flask-WTF
- Password strength validation and forced password changes
- Account locking after failed login attempts
- Security event logging to `logs/security.log`
- Rate limiting on API endpoints (exempt for skeeball real-time updates)
- File upload validation with type and size restrictions
- Session protection with secure cookies

#### Database & Storage
- SQLite database with SQLAlchemy ORM
- Flask-Migrate for database migrations
- Backup encryption with Fernet keys
- Database location flexibility (root or instance folder)
- Photo storage with image compression (max 1200x1200, 85% quality)
- Optional AWS S3 cloud storage for maintenance photos

#### Documentation
- Comprehensive README.md with installation and usage instructions
- WARP.md for AI-assisted development with common commands
- GitHub Wiki with feature documentation
- Skeeball Integration guide with hardware requirements
- Deployment checklist for production setup
- Troubleshooting guides

### Changed
- Control panel updates every 500ms (was 2000ms)
- Statistics page updates every 1000ms (was 5000ms)
- Skeeball control panel header uses clean styling (removed glitch effect)
- Manage users table simplified (removed email column)
- Add User button maintains text visibility after clicking
- Stats API now correctly displays total score, best score, and average score

### Fixed
- Stats API properly loads game history from skeeball_state.json
- load_realtime_state() reloads game_history on each request for accurate stats
- load_state_from_file() uses absolute path for state file location
- Real-time game state synchronization fully functional
- Skeeball control panel updates on ball completion, not just game start
- Low stock alerts now display correctly on dashboard
- Rate limiting exemption for skeeball API endpoints

### Technical Details
- Flask 3.0.0 backend with SQLAlchemy 2.0+
- Python 3.10+ required
- Chart.js for analytics visualization
- ReportLab 4.0.6 for PDF generation
- Pandas 2.1.4 and NumPy 1.26.2 for data analysis
- Flask-Limiter for rate limiting
- Cryptography library for backup encryption

### Deployment
- Supports Linux environments (tested on Fedora and Raspberry Pi OS)
- Development server via `python app.py`
- Production deployment with Gunicorn recommended
- Automated backup cron job via `scripts/setup_daily_backup.sh`
- Multiple Git branches: master, skeeball-integration, no-skeeball

---

**Full changelog**: https://github.com/digitalunconciousness/cabcrawl/commits/master
