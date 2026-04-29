# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [2026.03] - 2026-03-20

### Added
- CalVer versioning system (`src/__version__.py`) as single source of truth
- Parallel chapter generation — runs concurrently with subtitle translation via ThreadPoolExecutor
- `src/track_reorder.py` — extracted track reordering logic into dedicated module
- `src/__init__.py` — proper Python package initialization
- `config.ini.example` — template configuration file (no API keys)
- `requirements.txt` — pip dependency manifest
- Module-level docstrings on all source files
- README.md with full project documentation
- This CHANGELOG

### Fixed
- **B1**: Removed dead `select_mkv_file_gui()` function (never called)
- **B2**: Non-atomic `data.json` write in theme cache — now uses tmp+replace pattern
- **B3**: Unreachable `ContentBlockedError` exception handler — reordered except clauses
- Redundant `import re` statements inside function bodies (already imported at module level)
- `(OSError, Exception)` redundant exception tuple simplified to `Exception`
- Type annotations: `Optional[X]` → `X | None` syntax, added missing `| None` for nullable params

### Changed
- Version string updated from `v21_multimode` to CalVer `2026.03`
- Duplicated chapter-embedding logic (~50 lines × 2) extracted into `_embed_chapters_standalone()` helper
- `reorder_tracks()` moved from main script to `src/track_reorder.py` for maintainability

### Previous versions
- v21 (2025): Multi-mode input (Sonarr/Radarr/Standalone), anime chapter generation,
  title lookup, TTL cache, result validation. See `.sisyphus/plans/` in v21 folder for details.
