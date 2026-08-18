# Generacion_Sub_AI

Automatic MKV subtitle translation and anime chapter generation.

Translates embedded subtitles in MKV files using **universal OpenAI-compatible APIs** (agnostic provider),
with optional automatic anime OP/ED chapter detection via audio correlation with animethemes.moe.

## Features

- **Multi-mode input**: Sonarr (env vars), Radarr (env vars), Standalone (CLI/GUI)
- **Universal LLM API translation**: Batch translation with recursive fallback, rate limit handling
  - Compatible with **any OpenAI-format endpoint**: OpenRouter, OpenAI, Together, Groq, Ollama, vLLM, LM Studio, etc.
- **ASS/SSA tag preservation**: Formatting tags are extracted before translation and restored after
- **Anime chapter generation**: Automatic OP/ED detection via cross-correlation with animethemes.moe theme audio
- **Title lookup**: English→Romaji title resolution via animetitles.xml for better theme search accuracy
- **Smart track reordering**: Prioritizes Latin American Spanish > European Spanish > Other languages
- **Translation cache**: JSON-based cache avoids re-translating identical lines
- **Parallel processing**: Chapter generation runs concurrently with subtitle translation
- **Batch Processing**: Fully supports Sonarr mass rename/grab events containing multiple files.
- **Post-translation validation & correction**: Automatic QA with mini-batch re-translation of errors (1 API call vs N)

## Requirements

- Python 3.10+
- ffmpeg, mkvmerge, mkvextract (system binaries)
- See `requirements.txt` for Python packages

## Installation

```bash
pip install -r requirements.txt
```

Copy `config.ini.example` to `config.ini` and set your API key and base URL.

## Configuration

All settings are in `config.ini` (copy from `config.ini.example`):

| Section | Key | Description |
|---------|-----|-------------|
| `[API]` | `api_key` | API key del proveedor (OpenRouter, OpenAI, Together, Groq, Ollama, etc.) |
| `[API]` | `base_url` | URL base compatible OpenAI (ej: https://openrouter.ai/api/v1, http://localhost:11434/v1) |
| `[LLM_SETTINGS]` | `model` | Modelo a usar (ej: openai/gpt-4o-mini, llama3.2, auto/best-free) |
| `[LLM_SETTINGS]` | `batch_size` | Líneas por lote API (default: 50) |
| `[LLM_SETTINGS]` | `enable_translation_cache` | Caché en memoria para líneas repetidas (yes/no) |
| `[MKV_OUTPUT]` | `output_action` | `remux` (incrustar en MKV) o `save_separate_sub` |
| `[MKV_OUTPUT]` | `replace_original_mkv` | Reemplazar MKV original (yes/no) |
| `[MKV_OUTPUT]` | `target_language_name` | Idioma destino para prompts y código MKV |
| `[MKV_OUTPUT]` | `preferred_source_lang` | Código idioma fuente preferido (ej: fre) |
| `[CHAPTERS]` | `enabled` | Generar capítulos anime OP/ED (yes/no) |
| `[CHAPTERS]` | `theme_cache_dir` | Directorio caché temas descargados |
| `[CHAPTERS]` | `anime_path` | Solo generar capítulos para series bajo esta ruta |
| `[CHAPTERS]` | `max_theme_cache_mb` | Tamaño máximo caché temas en MB (default: 1024) |
| `[CHAPTERS]` | `theme_cache_ttl_days` | Días TTL caché temas (default: 120) |

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
├── __version__.py       — CalVer version (2026.08.1)
├── config_manager.py    — config.ini parser
├── api_client.py        — Universal LLM API client (OpenAI-compatible) + retry logic
├── model_manager.py     — Model rotation + rate limit tracking
├── cache_manager.py     — Translation result cache
├── tag_handler.py       — ASS/SSA tag extraction/restoration
├── line_numbering.py    — Batch line numbering for API
├── translation_validator.py — Post-translation QA + mini-batch correction
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