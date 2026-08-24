# CONTEXT.md — Dominio de Generacion_Sub_AI

Glosario vivo del proyecto. Los términos aquí definidos son los nombres
oficiales para hablar del dominio y de la arquitectura (módulos, seams,
interfaces). Si un concepto nuevo aparece en un refactor, se nombra aquí.

## Glosario

- **Configuración efectiva** (`Config`, `src/config_manager.py`): la configuración
  ya resuelta — defaults + `config.ini` + avisos — expuesta como objeto
  inmutable con atributos tipados planos. Única fuente de verdad para nombres,
  tipos y defaults de claves; las secciones del INI son almacenaje, no interfaz.
  Toda clave nueva nace en la tabla SPEC, se documenta en `config.ini.example`
  y se consume como atributo.

- **Contexto de archivo** (`FileContext`, `src/config_manager.py`): lo variable
  entre MKVs — ruta, títulos de serie/episodio, temporada, y si los capítulos
  OP/ED están habilitados para este archivo. Nace en `_extract_contexts`,
  lo construye `process_file` y viaja como parámetro; nunca se inyecta en la
  Configuración efectiva (que es inmutable).

- **Protocolo de traducción** (`src/protocol.py`): dueño del vocabulario que
  cruza la frontera del LLM — gramática `[N]:` (empieza en 1), alfabeto
  `__TAGn__`, y sentinelas `[[KIND: mensaje]]` con kinds registrados
  (`ERROR_API_SINGLE`, `ERROR_FATAL_TRADUCTOR`). Publica `REQUIRED_PROMPT_TOKENS`;
  todo template de prompt debe contenerlos para seguir de acuerdo con el parser.
  Un texto legítimo como `[[OVA]]` no es un sentinela.

- **Traducción**: convertir las líneas de la pista fuente al idioma objetivo
  vía API compatible OpenAI, por lotes numerados `[N]:` con placeholders
  `__TAGn__` preservados y fallback recursivo línea a línea.

- **Pista fuente / pista objetivo**: subtítulo de origen (preferencia
  `preferred_source_lang`) vs el sub en idioma destino cuya existencia corta
  el pipeline (dispara reordenamiento o salida temprana).

- **Remux**: incrustar el subtítulo traducido (y capítulos) en el MKV con
  mkvmerge, respetando `output_action` (remux vs `.srt` aparte) y la política
  de reemplazo del original.

- **Capítulos OP/ED**: marcadores de apertura/cierre generados por
  correlación de audio contra temas de animethemes.moe, gated por
  `chapters_enabled` + `anime_path`.

- **Caché de traducción** (`cache/translation_cache.json`): texto original →
  texto traducido, persistido entre ejecuciones.
- **Caché de temas** (`theme_cache_dir`): audios de OP/ED descargados, con
  poda por TTL/tamaño al final del lote.

## Decisiones registradas

- 2026-08-23: la escalera de reintentos API es interna (3 intentos, base 2s);
  las claves `api_max_retries`/`api_retry_initial_delay` fueron eliminadas.
  No re-introducirlas sin una ADR que justifique re-exponerlas.
- 2026-08-23: `rate_limit_max_global_retries` eliminada — la rotación de
  modelos ante 429 no existe en el producto. Implementarla es un proyecto
  propio, no un flag.
