# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- Future features

## [v1.0.8] - 2026-07-09

### Added
- Enforce `auto_delete_minutes` group setting: whispers created in a group now
  automatically get an expiration time when the group setting is > 0.
  The sender's explicit `auto_delete_hours` (when non-zero) takes priority over
  the group default. Applies to all whisper types (everyone, first_one,
  first_three, custom).

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
