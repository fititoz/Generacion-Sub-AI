# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [2026.08.5] - 2026-08-20

### Added
- Constantes centralizadas de binarios externos en `src/constants.py` (`BIN_MKVMERGE`, `BIN_MKVEXTRACT`, `BIN_FFMPEG`) como única fuente de verdad.

### Changed
- `check_mkvtoolnix_tools` usa las constantes de binarios en lugar de literales.
- `chapter_generator.py` usa `BIN_FFMPEG` para extracción/conversión de audio.
- Comentario obsoleto actualizado: referencia a `OpenAIClient`/`openai_client.py` → `APIClient`/`api_client.py`.

### Removed
- `src/model_manager.py` (código muerto: clase `ModelManager` no referenciada por el cliente actual).
- `src/__pycache__/openai_client.cpython-314.pyc` (bytecode huérfano de módulo ya renombrado).
- `cache/model_state.json` (archivo huérfano escrito solo por `ModelManager`).

### Fixed
- `SyntaxError` en `Generacion_Sub_AI.py` (f-string anidada con barra invertida, inválida en Python 3.11): extraído `name_part` fuera de la f-string.

## [2026.08.1] - 2026-08-18

### Added
- **Universal LLM API client** (`src/api_client.py`) — agnóstico de proveedor, compatible con cualquier endpoint OpenAI (OpenRouter, OpenAI, Together, Groq, Ollama, vLLM, LM Studio, etc.)
- **Mini-batch error correction** en `TranslationCorrector` — agrupa líneas con errores críticos en 1 sola llamada API batch (vs N llamadas individuales), con fallback automático a línea individual
- 6 nuevos checks críticos en validador: `empty`, `ratio bajo`, `coma huérfana`, `pérdida línea corta`, `ratio bajo sin tags`, `artefacto numeración batch`
- `config.ini.example` — plantilla segura sin claves reales
- Reordenamiento lógico de `config.ini`: `[API]`, `[LLM_SETTINGS]`, `[MKV_OUTPUT]`, `[PROMPTS]`, `[CHAPTERS]`, `[DEBUG]`

### Changed
- **BREAKING**: Replaced Google Gemini API con cliente universal OpenAI-compatible
- `requirements.txt`: `google-genai` → `openai`
- `config.ini`: Eliminados `gemini_api_key`, `preferred_models`; añadidos `api_key`, `base_url`, `model` + reordenado
- `translated_track_name` default: "Español Latino (Gemini AI)" → "Español Latino (AI)"
- `src/config_manager.py`: Parsea nueva estructura de config
- `Generacion_Sub_AI.py`: Imports actualizados a `APIClient` (antes `OpenAIClient`)
- `src/translation_validator.py`: `TranslationCorrector` usa `api_client` + lógica mini-batch
- Cache file: `gemini_translation_cache.json` → `translation_cache.json`
- Archivo renombrado: `src/openai_client.py` → `src/api_client.py` (clase `APIClient`)
- Comentarios en código: referencias "openai_client" → "api_client"
- Version bumped a CalVer 2026.08.1

### Fixed
- Falsos positivos en validador: comparación de tags consistente (orig vs trans con placeholders ya restaurados)
- `tag_handler.py`: `restore_tags` robustecido — no corrompe texto si falta placeholder en respuesta
- `line_numbering.py`: Rechazo temprano de formato erróneo "N. texto" sin corchetes

### Removed
- `src/gemini_client.py`: Reemplazado por `src/api_client.py`
- Scripts de prueba locales: `_test_punct.py`, `_test_validator.py`, `_test_final.py`

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

