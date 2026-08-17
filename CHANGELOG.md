# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [2026.08.1] - 2026-08-17

### Added
- OpenAI-compatible API client (`src/openai_client.py`) supporting any provider with `base_url` + `api_key` (OpenRouter, Together, Groq, Ollama, etc.)
- New configuration options in `config.ini`: `api_key`, `base_url`, `model`

### Changed
- **BREAKING**: Replaced Google Gemini API with OpenAI-compatible API
- `requirements.txt`: `google-genai` → `openai`
- `config.ini`: Removed `gemini_api_key` and `preferred_models`; added `api_key`, `base_url`, `model`
- `translated_track_name` default changed from "Español Latino (Gemini AI)" to "Español Latino (AI)"
- `src/config_manager.py`: Parses new API configuration fields
- `Generacion_Sub_AI.py`: Updated imports and references to use `OpenAIClient`
- `src/translation_validator.py`: `TranslationCorrector` now uses `api_client`
- Cache file renamed from `gemini_translation_cache.json` to `translation_cache.json`
- Version bumped to CalVer 2026.08.1

### Removed
- `src/gemini_client.py`: Replaced by `src/openai_client.py`

## [2026.05.3] - 2026-05-06

### Added
- Python Auto-Updater: Automatic self-updating from GitHub releases.## [2026.05.2] - 2026-05-06

### Added
- Smart theme cache pruning (TTL and size-based) to manage `animethemes` audio storage.

## [2026.05.1] - 2026-05-06

### Added
- Batch Processing support for Sonarr mass operations (processes multiple pipe-separated files in a single run).

## [2026.05] - 2026-05-06

### Added
- Sonarr/Radarr environment variable debug logging on execution.

### Fixed
- Anime chapter lookup ignoring `x-jat` (romaji) aliases in `animetitles.xml`.
- Season matching failing on Spanish season titles (`Temporada X`), short season tags (`S X`), and titles containing years `(YYYY)`.

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

