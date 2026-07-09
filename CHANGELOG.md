# Changelog

All notable changes to this project will be documented in this file.

## [v1.1.0] - 2026-07-09

### Added
- Public whispers with group-level enable/disable setting
- Read notifications with detailed HTML and simple receipt modes
- Group-level auto-delete default policy (`auto_delete_minutes`)
- Per-group settings admin panel for public whispers, read notifications,
  anonymous mode, and auto-delete defaults
- Comprehensive Features section in README.md

### Changed
- Replaced deprecated `datetime.utcnow()` with timezone-aware
  `datetime.now(timezone.utc)` across the codebase
- Group `auto_delete_minutes` now applies automatically when the sender
  does not specify an explicit `auto_delete_hours` value

### Fixed
- Public whisper read notifications now reliably notify the sender in DM
- Whisper keyboard consistency when toggling group settings

## [v1.0.9] - 2026-07-09

### Changed
- Improved auto delete settings UI in group settings panel
- Better visual feedback when toggling auto-delete presets

---

## [v1.0.8] - 2026-07-09

### Added
- Enforce `auto_delete_minutes` group setting: whispers created in a group now
  automatically get an expiration time when the group setting is > 0.
  The sender's explicit `auto_delete_hours` (when non-zero) takes priority over
  the group default. Applies to all whisper types (everyone, first_one,
  first_three, custom).

---

## [v1.0.7] - 2026-07-09

### Added
- Group settings admin panel with dedicated UI
- Enforce public whisper group setting: public whispers can be enabled or
  disabled per group
- Enforce read notification group setting: read receipts can be toggled
  per group

---

## [v1.0.6] - 2026-07-08

### Added
- Group settings foundation and data model
- Public whisper read notifications sent to sender via DM

### Changed
- Improved public whisper keyboard layout and interaction flow

---

## [v1.0.1] - 2026-07-08

### Changed
- Cleaned repository
- Added .gitignore
- Removed runtime files from Git
- Added README
- Added MIT License

---

## [v1.0.0] - 2026-07-08

### Added
- Initial stable release
- Whisper system
- Reply system
- Dashboard
- REST API
- Enterprise features
- Tests
