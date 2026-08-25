# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Sin publicar]

### Added
- **Pisos de versión en dependencias** (`requirements.txt` + instalador runtime vía `PACKAGE_FLOORS`): toda instalación usa `paquete>=última verificada` al 2026-08-23 (openai 3.3.1, pysubs2 1.8.1, pymkv 1.0.8, numpy 2.4.6, scipy 1.17.1, requests 2.34.2, soundfile 0.14.0); pip resuelve la mayor compatible si el host es más viejo.
- **`src/phases.py`** — fases testeables extraídas de `process_file` (probe `-J`, análisis pymkv, escaneo de pistas/objetivo, extracción, carga con cascada de encodings, recolección/traducción/aplicación). `process_file` baja de ~990 a 806 líneas y su parte media ya es testeable sin importar el entry script.
- **Módulo `src/remux.py`** — dueño único de la ejecución mkvmerge: exit-codes oficiales (rc≤1 éxito), timeout único de 900s, temporales atómicos con `os.replace`, validación ligera siempre y profunda (probe `-J`: video + duración ±10s) antes de cualquier reemplazo destructivo. Tres envoltorios (`embed_translation`, `embed_chapters`, `reorder_and_save`) mantienen los call sites casi intactos; stub binario como segundo adapter para tests.

### Fixed
- **Cierre del bucle infinito de reencolado (S1 principal)**: `mkvmerge` exit code 1 son *warnings* con salida válida según la documentación oficial — ya no se trata como error que borraba el mux correcto y hacía que Sonarr reencolara el mismo episodio para siempre. Los tres sitios de invocación (traducción, capítulos standalone, reordenamiento) comparten ahora política, timeout de 900s y limpieza garantizada de temporales.

### Changed
- **Pasada de documentación**: cobertura completa de docstrings en funciones y clases del producto (57 añadidos), comentarios obsoletos corregidos o eliminados (header de versión engañoso, marcadores "CORREGIDO", notas estilo changelog "NUEVO/MEJORA") y banners de fase alineados con la delegación a `src/remux.py`/`src/phases.py`.

### Fixed
- **SIGTERM mata el proceso de verdad**: el handler guardaba caché y luego hacía `sys.exit(0)`, elevando una `SystemExit` que `process_file` tragaba — el batch continuaba tras el kill y el proceso terminaba con código 0. Ahora guarda caché y re-envía la señal con el handler por defecto.
- **`.ogg/.opus/.webm` van directos a ffmpeg** en el cargador de audio de capítulos: libsndfile falla siempre con esos formatos; antes se pagaba el intento fallido por cada tema (2× por episodio).
- **`und` sin tag ya no se selecciona como pista fuente**: un sub sin idioma suele ser ya el objetivo; traducirlo era eco español→español silencioso.
- **`reorder_tracks` ahora lee `language_ietf`**: un sub `es-419` etiquetado solo vía IETF era invisible para la priorización "Latino primero" (caía al tier genérico y perdía el default). Añadida normalización a minúsculas.
- **Literales `__TAGn__` en el texto original ya no corrompen la restauración**: se escapan durante la extracción (`__TAGLn__`) y se des-escapan al final — el primer placeholder real ya no puede chocar contra el literal.
- **La caché rechaza de verdad entradas con placeholders sin restaurar**: antes las logueaba y las guardaba igual, envenenando corridas futuras (`TranslationCache.set`).
- **Correcciones del validador ya no se neutralizan ni se pierden**: las líneas flaggeadas invalidan su entrada stale antes de re-traducirse (el fallback individual releía el valor malo) y la corrección exitosa se re-cachea — elimina el costo API recurrente por corrida observado como `Corregidas=2/2` constante.
- Nuevo `TranslationCache.delete()` para invalidación puntual.

## [2026.08.9] - 2026-08-23

### Changed
- **Configuración efectiva (refactor arquitectural)**: `src/config_manager.py` ahora es el dueño único de las claves — una tabla (`SPEC`) define sección, tipo y default de cada una, y expone `Config`, un objeto inmutable con atributos tipados (`cfg.batch_size`). Los ~118 accesos por diccionario/cadena fueron migrados a atributos; añadir o renombrar una clave ya no puede hacerse en un solo archivo y romper otro en silencio.
- **Prompt batch endurecido** (reglas nuevas/ampliadas, sincronizadas entre defaults e `config.ini.example`): numeración empieza en `[1]` nunca `[0]`; número entre corchetes sin markdown; sin preámbulos ni frases de cierre; horarios tipo `21:45` dentro del texto tal cual; tuteo determinista (voseo solo si el nombre del idioma lo indica); prohibido devolver el original sin traducir; longitud natural similar a la original.
- **Prompt individual endurecido** (`single_template`): alinea sus reglas con el batch — tono determinista (tuteo/voseo), preservación de nombres y honoríficos, anti copy-through, placeholders intactos, sin markdown ni preámbulos, longitud natural. Evita que las líneas re-traducidas (fallback/corrección) cambien de estilo respecto al resto del episodio.
- **Protocolo de traducción unificado** (`src/protocol.py`, nuevo módulo dueño del vocabulario de cable): gramática `[N]:`, alfabeto `__TAGn__` y sentinelas `[[…]]` comparten ahora una única definición y predicado — antes había tres criterios distintos repartidos en tres archivos.
- **Subtítulos legítimos con corchetes dobles ya no se descartan**: el corte post-traducción usaba `startswith("[[")`, que se comía texto real tipo `[[OVA]]`; ahora solo los sentinelas con kind registrado (`ERROR_API_SINGLE`, `ERROR_FATAL_TRADUCTOR`) cuentan como error.
- **Truncado por números con dos puntos corregido en el parseo principal**: `[2]: El reloj marcaba 21:45` devolvía "El reloj marcaba" — el marcador siguiente ahora debe empezar en línea nueva.
- **Aviso de claves desconocidas**: las claves presentes en `config.ini` que no pertenecen a la tabla se registran como warning (`clave_desconocida: [SECCIÓN] clave (ignorada)`).
- **Contexto de archivo**: `FileContext` (títulos, temporada, capítulos habilitados) viaja ahora como parámetro inmutable; `process_file` dejó de mutar y restaurar el dict de configuración (elimina la carrera con el hilo de capítulos).

### Added
- `theme_portion` publicada en `[CHAPTERS]` (0.9) — antes era constante interna consumida sin estar documentada.
- `debug_max_chars` añadida a los defaults internos.
- `tests/test_config_parity.py`: cruza example ↔ tabla ↔ tipos (requiere `pytest`, solo desarrollo).

### Fixed
- **Privacidad: `debug_translation` ahora es `false` por defecto** (example y defaults internos). Con `true` se volcaban subtítulos completos al log y consola — hallazgo S1 de la auditoría.
- **Detector de rutas de otra máquina**: si `anime_path`, `theme_cache_dir` o `mkvtoolnix_dir` apuntan a rutas inexistentes en el host actual (clásico al copiar config.ini entre Windows y Sonarr), se registra warning `ruta_inexistente: ...`. Caso real: `anime_path = C:\Users\...` en el contenedor Linux dejaba los capítulos muertos en silencio.
- **Respuestas del proveedor con `choices` vacío/None** ('NoneType' object is not subscriptable, observado 40 veces en una corrida real): ahora se detectan explícitamente y son reintentables en vez de matar el chunk completo.
- **Flood de `_trace.py close.started/close.complete`** (trazas internas del SDK HTTP) en el archivo de log: filtradas a nivel de handler, independiente del nombre del logger emisor.
- **Duplicación "líneas líneas"** en el prompt batch ("las siguientes 50 líneas líneas"): `batch_size_info` llevaba la palabra y el template también.
- **Truncado por números con dos puntos en el parseo principal**: `[2]: El reloj marcaba 21:45` devolvía "El reloj marcaba" — el marcador siguiente ahora debe empezar en línea nueva.
- **`anime_path` vuelve a funcionar**: la clave se leía con el nombre equivocado (`CHAPTERS_ANIME_PATH`); el filtro documentado nunca se aplicaba.
- **Comentario desactualizado en el example**: los deps de capítulos NO vienen vía docker-mods; los instala pip en runtime. Añadido consejo de `max_tokens = 4000` para ASS muy taggeados y nota de rutas por-máquina en las instrucciones.

### Removed
- `BATCH_FAILURE_INDICATOR` (constante sin referencias desde su nacimiento) y el bloque `except LineCountMismatchError` en `api_client.py` (excepción que nadie lanza; la clase permanece en `exceptions.py` por compatibilidad).
- Claves sin efecto real, con sus entradas del ejemplo:
  - `api_max_retries` / `api_retry_initial_delay`: el código leía otros nombres; la escalera de reintentos es interna (3 intentos, base 2s con backoff exponencial).
  - `rate_limit_max_global_retries`: la rotación de modelos ante 429 no existe en el producto.
  - `add_subs_to_mkv`: la acción real la define `output_action`.
  Los usuarios con esas claves en su `config.ini` verán un warning y serán ignoradas.

## [2026.08.8] - 2026-08-22

### Fixed
- **Ejecución directa en Linux/Sonarr (exit 127)**: `Generacion_Sub_AI.py` y `src/constants.py` estaban guardados en el repo con finales de línea CRLF de Windows. El shebang quedaba como `#!/usr/bin/env python3\r` y el kernel buscaba un intérprete inexistente (`python3<CR>`). Ambos archivos normalizados a LF.
- **Prevención**: nuevo `.gitattributes` con `* text=auto eol=lf` — git normaliza todo texto a LF en el repo y en los zipballs de release, independiente del SO desde donde se comitee.

## [2026.08.7] - 2026-08-21

### Added
- **Chunking real por `batch_size`** (`translate_recursive_fallback`): los lotes se dividen en grupos de N líneas (default 50). Antes se enviaban TODAS las líneas del archivo en una sola llamada y el modelo cortaba la salida por `max_tokens` (~100 líneas buenas, resto vacío). Fallo aislado por chunk: si un lote revienta, los ya traducidos se conservan.
- **Progreso visible en consola**: `Batch N/M: enviando K líneas (batch_size=50)...` por lote, y `[individual] X/Y (línea Z): OK|ERROR` en el fallback línea-a-línea.
- **Parseo parcial tolerante**: si una respuesta de batch vuelve incompleta (`finish_reason=length`), se conservan las líneas bien parseadas y solo las faltantes van a reintento. Antes se descartaba el lote entero y se reenviaba completo.
- **Limpieza de eco `original -> traducción`** (`strip_source_echo`): algunos modelos ignoran el formato numerado y devuelven `"inglés -> español"`; ese eco ya no llega al subtítulo final.
- **Instrumentación de debug ampliada**: volcado crudo de respuestas API por batch (truncable con nuevo `[DEBUG] debug_max_chars`, default 800), decisiones del fallback recursivo, restauración de placeholders tag-por-tag, dump original→traducción en el validador y conteo de entradas de caché.

### Changed
- **Pacing efectivo**: `api_call_delay` (default 5s) ahora se aplica antes de cada petición. Era config muerta: las llamadas se disparaban sin pausa (~2 req/s sostenidos) hasta que NVIDIA/OpenRouter bloqueaba con 429.
- **Backoff real ante 429**: espera `rate_limit_wait_seconds` (60s) o el header `Retry-After` del servidor si lo envía (acotado a 120s). Antes solo esperaba 4/8/16s.
- **Reintentos internos del SDK desactivados** (`OpenAI(max_retries=0)`): los micro-reintentos sub-segundo del SDK openai quemaban cuota justo cuando había que darle aire a la API; ahora la única capa de reintento es la propia, con backoff real.
- **Test de conexión inicial vía `_call_api`**: hereda pacing + reintentos + espera de rate limit. Si la API rachea 429 al arrancar, el script espera con paciencia en vez de abortar.

### Fixed
- **404/500 transitorios ahora reintentables**: `_is_retryable_error` solo reconocía 502/503/504 — un `Error code: 404` o `500` bajo carga quedaba como `[[ERROR_API_SINGLE]]` permanente aunque reintentar funcionara (visible en logs: 404 seguidos de 200 OK).
- **`LineCountMismatchError` lanzado con firma incorrecta**: el propio `raise` explotaba con `TypeError`, enmascarando el error real y descartando traducciones ya obtenidas del lote.

## [2026.08.6] - 2026-08-21

### Fixed
- **NameError crítico en ejecución standalone**: `Generacion_Sub_AI.py` usaba `BIN_MKVMERGE`/`BIN_MKVEXTRACT` sin importarlos desde `src.constants`. Añadidos al import (rompía `check_mkvtoolnix_tools` al iniciar).
- **Updater**: bug de indentación — el bloque de descarga/extracción/reescritura de archivos estaba fuera del `if latest_parts > curr_parts`, ejecutándose en cada run aunque no hubiera versión nueva (sobrescritura innecesaria). Ahora solo corre cuando hay actualización.
- **Updater**: el endpoint `/releases/latest` devolvía 404 (solo existía el tag, no el Release). Manejo de 404 ahora es informativo en lugar de warning.

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

