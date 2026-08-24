# Generacion_Sub_AI

Automatic MKV subtitle translation and anime chapter generation.

Translates embedded subtitles in MKV files using **universal OpenAI-compatible APIs** (provider-agnostic),
with optional automatic anime OP/ED chapter detection via audio correlation with animethemes.moe.

Current release: **2026.08.9** — see [CHANGELOG.md](CHANGELOG.md).

## Features

- **Multi-mode input**: Sonarr (env vars), Radarr (env vars), Standalone (CLI/GUI)
- **Universal LLM API translation**: batch translation with chunked recursive fallback, rate-limit
  handling and provider-quirk retries
  - Compatible with **any OpenAI-format endpoint**: OpenRouter, OpenAI, Together, Groq, Ollama, vLLM, LM Studio, etc.
- **Translation wire protocol** (`src/protocol.py`): single owner for the `[N]:` numbering grammar,
  `__TAGn__` placeholder alphabet and `[[…]]` error sentinels — prompt templates, parser and validators
  can no longer drift apart silently
- **Hardened prompts**: 1-based numbering, no markdown around markers, no preambles, deterministic
  tone (tuteo/voseo), preservation of names & honorifics — identical rules for batch, fallback and correction passes
- **ASS/SSA tag preservation**: formatting tags are extracted before translation and restored after
- **Anime chapter generation**: OP/ED detection via cross-correlation with animethemes.moe theme audio,
  romaji title resolution, OGM chapter writing and embedding
- **Smart track reordering**: prioritizes Latin American Spanish > European Spanish > other languages;
  idempotent (re-running on an already-ordered MKV is a no-op)
- **Effective configuration** (`Config`): one table owns every key's name/type/default; unknown keys and
  host-mismatched paths are reported as warnings instead of failing silently
- **Translation cache**: JSON-based cache avoids re-translating identical lines across runs
- **Parallel processing**: chapter generation runs concurrently with subtitle translation; a chapters or
  mux failure never takes down the translated subtitle
- **Batch processing**: fully supports Sonarr mass rename/grab events containing multiple files
- **Post-translation validation & correction**: automatic QA with mini-batch re-translation of errors

## Requirements

- Python 3.10+
- ffmpeg, mkvmerge, mkvextract (system binaries; MKVToolNix)
- Python packages from `requirements.txt` (`openai`, `pysubs2`, `pymkv`; chapter extras optional)

## Installation

```bash
pip install -r requirements.txt
```

Copy `config.ini.example` to `config.ini` and set your API key and base URL.
Paths in the config are **per machine** — if you copy the file between hosts,
check `mkvtoolnix_dir`, `theme_cache_dir` and `anime_path` (the loader warns
about paths that do not exist locally).

## Configuration

All settings live in `config.ini` (copy from `config.ini.example`). Key entries:

| Section | Key | Description |
|---------|-----|-------------|
| `[API]` | `api_key` | API key del proveedor (OpenRouter, OpenAI, Together, Groq, Ollama, etc.) |
| `[API]` | `base_url` | URL base compatible OpenAI (ej: https://openrouter.ai/api/v1) |
| `[LLM_SETTINGS]` | `model` | Modelo a usar (ej: openai/gpt-4o-mini, llama3.2, auto/best-free) |
| `[LLM_SETTINGS]` | `batch_size` | Líneas por lote API (default: 50) |
| `[LLM_SETTINGS]` | `max_tokens` | Sube a 4000 con ASS muy taggeados y lotes grandes (evita cortes) |
| `[LLM_SETTINGS]` | `enable_translation_cache` | Caché de traducciones entre ejecuciones (yes/no) |
| `[MKV_OUTPUT]` | `output_action` | `remux` (incrustar en el MKV; requerido para capítulos) o `save_separate_sub` |
| `[MKV_OUTPUT]` | `replace_original_mkv` | Reemplazar el MKV original (yes/no) |
| `[MKV_OUTPUT]` | `target_language_name` | Idioma destino para prompts y código MKV |
| `[MKV_OUTPUT]` | `preferred_source_lang` | Código de idioma fuente preferido (ej: fre) |
| `[CHAPTERS]` | `enabled` | Generar capítulos anime OP/ED (yes/no) |
| `[CHAPTERS]` | `anime_path` | Solo genera capítulos para series bajo esta ruta |
| `[CHAPTERS]` | `theme_cache_dir` | Caché persistente de temas descargados |
| `[DEBUG]` | `debug_translation` | Traza completa al log (**default: false** — vuelca subtítulos al log) |

Removed keys (`api_max_retries`, `api_retry_initial_delay`, `rate_limit_max_global_retries`,
`add_subs_to_mkv`) are ignored with a warning; the retry ladder is internal (3 attempts, 2s base).

## Usage

### Sonarr / Radarr

Configure as a custom script on Import/Download. The script auto-detects the mode
from environment variables (`sonarr_episodefile_path`, `radarr_eventtype`) and is
fully headless. Mass events with several files are processed sequentially.

### Standalone

```bash
python Generacion_Sub_AI.py --file "/path/to/file.mkv" --series "Anime Name" --season 1
```

`--series` improves the prompt context and the theme search; without it the script
prompts for an MKV via CLI/tkinter.

### Chapters

Chapters require `output_action = remux` and an MKV without existing chapters.
The pipeline: romaji title lookup → animethemes.moe search with fuzzy name validation →
theme download → episode audio extraction → DSP cross-correlation → OGM chapter file →
embedded during the remux/reorder pass. Failures here never affect the translated subtitle.

## Development

```bash
python -m pytest tests/ -q          # configuration parity + translation protocol contract
python -m py_compile Generacion_Sub_AI.py src/*.py
```

Domain glossary and architecture decisions: [CONTEXT.md](CONTEXT.md).
Release history: [CHANGELOG.md](CHANGELOG.md).

## Docker

Designed for LinuxServer.io-style containers. Chapter dependencies (`numpy`, `scipy`,
`requests`, `soundfile`) are installed automatically at first use if missing. The script
runs headless with no interactive prompts in Sonarr/Radarr mode.

## Architecture

```
Generacion_Sub_AI.py     — Entry point, mode detection, orchestration
src/
├── __version__.py       — CalVer version (2026.08.9), single source of truth
├── config_manager.py    — Effective configuration: key table + frozen Config + FileContext
├── protocol.py          — Translation wire protocol ([N]: grammar, __TAGn__, [[sentinels]])
├── api_client.py        — Universal LLM API client (OpenAI-compatible) + retry/backoff
├── cache_manager.py     — Translation result cache
├── tag_handler.py       — ASS/SSA tag extraction/restoration
├── line_numbering.py    — Batch line numbering + tolerant response parsing
├── translation_validator.py — Post-translation QA + mini-batch correction
├── chapter_generator.py — OP/ED audio correlation engine
├── title_lookup.py      — English→Romaji title lookup
├── track_reorder.py     — MKV track reordering
├── dependencies.py      — Runtime dependency checker/installer
├── constants.py         — Shared constants
├── exceptions.py        — Custom exception hierarchy
└── logging_setup.py     — Logging configuration (SDK noise filtered)
```

## Versioning

This project uses [CalVer](https://calver.org/) with the format `YYYY.MM[.PATCH]`.
The version is defined in `src/__version__.py` as the single source of truth;
release tags must match it.

## License

Private project. Not licensed for redistribution.
