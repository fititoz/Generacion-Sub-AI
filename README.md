# Generacion_Sub_AI

Automatic MKV subtitle translation and anime chapter generation.

Translates embedded subtitles in MKV files using Google Gemini AI, with optional
automatic anime OP/ED chapter detection via audio correlation with animethemes.moe.

## Features

- **Multi-mode input**: Sonarr (env vars), Radarr (env vars), Standalone (CLI/GUI)
- **Gemini AI translation**: Batch translation with recursive fallback, model rotation, rate limit handling
- **ASS/SSA tag preservation**: Formatting tags are extracted before translation and restored after
- **Anime chapter generation**: Automatic OP/ED detection via cross-correlation with animethemes.moe theme audio
- **Title lookup**: English→Romaji title resolution via animetitles.xml for better theme search accuracy
- **Smart track reordering**: Prioritizes Latin American Spanish > European Spanish > Other languages
- **Translation cache**: JSON-based cache avoids re-translating identical lines
- **Parallel processing**: Chapter generation runs concurrently with subtitle translation

## Requirements

- Python 3.10+
- ffmpeg, mkvmerge, mkvextract (system binaries)
- See `requirements.txt` for Python packages

## Installation

```bash
pip install -r requirements.txt
```

Copy `config.ini.example` to `config.ini` and set your Gemini API key.

## Configuration

All settings are in `config.ini`:

| Section | Key | Description |
|---------|-----|-------------|
| `[API]` | `gemini_api_key` | Google Gemini API key |
| `[TRANSLATION]` | `preferred_models` | Ordered list of Gemini models to use |
| `[TRANSLATION]` | `batch_size` | Lines per API batch (default: 20) |
| `[SETTINGS]` | `output_action` | `remux` (embed in MKV) or `save_separate_sub` |
| `[SETTINGS]` | `replace_original_mkv` | Replace original MKV with translated version |
| `[CHAPTERS]` | `enabled` | Enable anime chapter generation (yes/no) |
| `[CHAPTERS]` | `theme_cache_dir` | Directory to cache downloaded theme audio |
| `[CHAPTERS]` | `anime_path` | Only generate chapters for series under this path |

## Usage

### Sonarr / Radarr

Configure as a custom script in Sonarr/Radarr. The script auto-detects the mode
from environment variables (`sonarr_episodefile_path`, `radarr_eventtype`).

### Standalone

```bash
python Generacion_Sub_AI.py
```

If no Sonarr/Radarr environment variables are detected, the script enters
standalone mode and prompts for an MKV file (or uses tkinter file dialog).

## Docker

Designed for LinuxServer.io Alpine-based containers. Install dependencies via
`linuxserver/docker-mods` at container creation time. The script runs headless
with no interactive prompts in Sonarr/Radarr mode.

## Architecture

```
Generacion_Sub_AI.py     — Entry point, mode detection, orchestration
src/
├── __version__.py       — CalVer version (2026.03)
├── config_manager.py    — config.ini parser
├── gemini_client.py     — Gemini API client + retry logic
├── model_manager.py     — Model rotation + rate limit tracking
├── cache_manager.py     — Translation result cache
├── tag_handler.py       — ASS/SSA tag extraction/restoration
├── line_numbering.py    — Batch line numbering for API
├── translation_validator.py — Post-translation QA
├── chapter_generator.py — OP/ED audio correlation engine
├── title_lookup.py      — English→Romaji title lookup
├── track_reorder.py     — MKV track reordering
├── dependencies.py      — Runtime dependency checker
├── constants.py         — Shared constants and defaults
├── exceptions.py        — Custom exception hierarchy
└── logging_setup.py     — Logging configuration
```

## Versioning

This project uses [CalVer](https://calver.org/) with the format `YYYY.MM[.PATCH]`.
The version is defined in `src/__version__.py` as the single source of truth.

## License

Private project. Not licensed for redistribution.
