#!/usr/bin/env python3
# Version: ver src/__version__.py (fuente única de verdad)
"""
Generacion_Sub_AI — MKV subtitle translator with anime chapter generation.

Entry point for Sonarr, Radarr, and standalone execution modes.
Coordinates subtitle extraction, API translation, post-translation
validation, MKV remuxing, track reordering, and chapter embedding.

Execution flow:
  1. Detect mode (Sonarr/Radarr/Standalone) from environment variables
  2. Extract context (MKV path, series title, season number)
  3. Check for existing target language subtitles
  4. Generate anime chapters in parallel (if enabled)
  5. Extract, translate, and validate subtitles
  6. Remux translated subtitles (and chapters) into MKV
"""
import os
import sys
import subprocess
import tempfile
import time
from pathlib import Path
import shutil
import signal
import logging
import json
import configparser
import shlex
import math
from concurrent.futures import ThreadPoolExecutor
from src.constants import TAG_REGEX, CACHE_DIR_NAME, CACHE_FILE_NAME, LOG_FILENAME, PLACEHOLDER_PREFIX, PLACEHOLDER_SUFFIX, REQUIRED_PACKAGES, BIN_MKVMERGE, BIN_MKVEXTRACT
import re
import importlib.metadata

# --- Shim de compatibilidad: pkg_resources (setuptools >= 82.0.0 lo removió) ---
if 'pkg_resources' not in sys.modules:
    import types as _types
    import importlib.resources
    _pkg_resources = _types.ModuleType('pkg_resources')
    _pkg_resources.__package__ = 'pkg_resources'
    class _DistributionNotFound(Exception):
        """Excepción shim compatible con pkg_resources.DistributionNotFound."""
        pass
    _pkg_resources.DistributionNotFound = _DistributionNotFound
    def _get_distribution(name):
        """Resuelve la distribución instalada o levanta el shim anterior."""
        try:
            return importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            raise _DistributionNotFound(name)
    _pkg_resources.get_distribution = _get_distribution
    def _resource_filename(package_name, resource_path):
        """Equivalente minimalista de resource_filename para setuptools>=82."""
        return str(importlib.resources.files(package_name).joinpath(resource_path))
    _pkg_resources.resource_filename = _resource_filename
    sys.modules['pkg_resources'] = _pkg_resources

from src.dependencies import check_and_install_dependencies

# --- Importaciones Principales ---
if not check_and_install_dependencies(): sys.exit("Saliendo por dependencias faltantes.")
try:
    import pysubs2
    from pymkv import MKVFile
    try:
        import tkinter as tk
        from tkinter import filedialog
        TKINTER_AVAILABLE = True
    except ImportError:
        TKINTER_AVAILABLE = False
except ImportError as e: print(f"ERROR FATAL importando ({e}).", file=sys.stderr); sys.exit(1)

from src.api_client import APIClient

# --- Configuración de Logging ---
from src.logging_setup import setup_logging

# --- Carga de Configuración ---
from src.config_manager import load_config, FileContext
from src.protocol import is_error_sentinel, make_error

from src.cache_manager import TranslationCache
from src.translation_validator import TranslationValidator, TranslationCorrector
from src.exceptions import SubtitleTranslationError, MKVOperationError, SubtitleParsingError
from src.chapter_generator import generate_chapters
from src.track_reorder import reorder_tracks
from src.remux import embed_chapters as _remux_embed_chapters, embed_translation
from src import phases

# --- Funciones Auxiliares ---
def find_executable(name, provided_path=None):
    """Localiza un binario: ruta provista (mkvtoolnix_dir) o PATH del sistema."""
    exec_name = name if os.name != 'nt' else f"{name}.exe"
    if provided_path:
        path_obj = Path(provided_path)
        if path_obj.is_file() and os.access(str(path_obj), os.X_OK):
            logging.info("Usando %s de ruta: %s", name, provided_path)
            return str(path_obj)
    executable_path = shutil.which(exec_name)
    if executable_path:
        return os.path.normpath(executable_path)
    logging.debug("No se encontró '%s'", name)
    return None
def check_mkvtoolnix_tools(cfg):
    """Verifica mkvextract/mkvmerge según la acción de salida. -> dict tools|None."""
    mkvtoolnix_dir = cfg.mkvtoolnix_dir
    mkvmerge_path = None
    mkvextract_path = None
    if mkvtoolnix_dir:
        mkvmerge_path = Path(mkvtoolnix_dir) / (f"{BIN_MKVMERGE}.exe" if os.name == 'nt' else BIN_MKVMERGE)
        mkvextract_path = Path(mkvtoolnix_dir) / (f"{BIN_MKVEXTRACT}.exe" if os.name == 'nt' else BIN_MKVEXTRACT)
    logging.debug("Verificando MKVToolNix...")
    mkvmerge = find_executable(BIN_MKVMERGE, str(mkvmerge_path) if mkvmerge_path else None)
    mkvextract = find_executable(BIN_MKVEXTRACT, str(mkvextract_path) if mkvextract_path else None)
    mkvextract_needed = True
    mkvmerge_needed = cfg.output_action == 'remux'
    tools_ok = True
    if mkvextract_needed and not mkvextract:
        logging.error("mkvextract necesario no encontrado.")
        tools_ok = False
    if mkvmerge_needed and not mkvmerge:
        logging.error("mkvmerge necesario para 'remux' no encontrado.")
        tools_ok = False
    if not tools_ok:
        return None
    if not mkvmerge_needed and not mkvmerge:
        logging.warning("mkvmerge no encontrado, pero no necesario.")
    found_tools = {}
    if mkvextract:
        found_tools['mkvextract'] = mkvextract
    if mkvmerge:
        found_tools['mkvmerge'] = mkvmerge
    try:
        MKVFile.mkvmerge_path = mkvmerge
    except Exception:
        pass
    logging.debug(f"MKVToolNix check OK. Encontrado: {list(found_tools.keys())}")
    return found_tools
def select_subtitle_track(tracks, track_codecs, cfg):
    """Selecciona la pista fuente traducible aplicando preferencia e idioma objetivo."""
    candidates = []
    image_subs_found = []
    preferred_lang = cfg.preferred_source_lang
    target_codes_set = cfg.target_language_codes_set
    logging.info("Buscando pista fuente (excluyendo: %s)...", target_codes_set)
    for t in tracks:
        tid = getattr(t, 'track_id', '?')
        ttype = getattr(t, 'track_type', '?')
        lang = getattr(t, 'language', 'und') or 'und'
        codec = track_codecs.get(tid, '?')
        is_image = codec != '?' and ('vobsub' in codec.lower() or 'pgs' in codec.lower())
        logging.debug("Evaluando ID=%s, T=%s, L=%s, C=%s", tid, ttype, lang, codec)
        if ttype == 'subtitles':
            if is_image:
                logging.info("  * ID=%s es imagen (%s), ignorando.", tid, codec)
                image_subs_found.append(t)
            elif lang == 'und':
                # Sin tag no hay fuente confiable: un sub 'und' suele ser ya
                # el idioma objetivo; traducirlo sería eco español->español.
                logging.debug("  - Ignorada ID=%s (idioma 'und' sin tag confiable como fuente).", tid)
            elif lang not in target_codes_set:
                logging.debug("  + Candidata ID=%s", tid)
                candidates.append(t)
            else:
                logging.debug("  - Ignorada ID=%s (Idioma '%s' en objetivo).", tid, lang)
    if not candidates:
        if image_subs_found:
            logging.error("No se encontraron pistas de texto traducibles.")
        else:
            logging.warning("No hay pistas candidatas (no en idioma objetivo).")
        return None
    pref = next((t for t in candidates if (getattr(t, 'language', 'und') or 'und') == preferred_lang), None)
    if pref:
        tid = getattr(pref, 'track_id', '?')
        lang = getattr(pref, 'language', 'und') or 'und'
        codec = track_codecs.get(tid, '?')
        logging.info("Selección: Pref ID %s (Lang '%s', Codec '%s').", tid, lang, codec)
        return pref
    if candidates:
        first = candidates[0]
        tid = getattr(first, 'track_id', '?')
        lang = getattr(first, 'language', 'und') or 'und'
        codec = track_codecs.get(tid, '?')
        logging.info("Selección: Primera no 'und' ID %s (Lang '%s', Codec '%s').", tid, lang, codec)
        return first
    first = candidates[0]
    tid = getattr(first, 'track_id', '?')
    lang = getattr(first, 'language', 'und') or 'und'
    codec = track_codecs.get(tid, '?')
    logging.info("Selección: Primera disponible ID %s (Lang '%s', Codec '%s').", tid, lang, codec)
    return first
def get_subtitle_extension(codec_id):
    """Mapea codec_id MKV a extensión de subtítulo ('.srt'/'.ass'/'.sub'/'.sup')."""
    if not codec_id or codec_id == '?':
        logging.warning("Codec ID no disponible ('%s'), asumiendo '.srt'.", codec_id)
        return '.srt'
    codec_lower = codec_id.lower()
    logging.debug("Extensión para Codec ID: '%s' -> Lower: '%s'", codec_id, codec_lower)
    if 'srt' in codec_lower or 'utf8' in codec_lower or 'subrip' in codec_lower:
        return '.srt'
    elif 'ssa' in codec_lower or 'ass' in codec_lower:
        return '.ass'
    elif 'vobsub' in codec_lower:
        logging.warning("Formato VobSub (imagen) detectado.")
        return '.sub'
    elif 'pgs' in codec_lower:
        logging.warning("Formato PGS (imagen) detectado.")
        return '.sup'
    else:
        logging.warning("Codec '%s' no reconocido. Asumiendo '.srt'.", codec_id)
        return '.srt'

from src.tag_handler import extract_tags, restore_tags

# --- Funciones de Traducción ---
# (Las funciones de traducción ahora se gestionan a través de la clase APIClient en src/api_client.py)



def _try_generate_chapters(file_ctx: FileContext, mkv_path: Path, mkv_info: dict, cfg, tmpdir_path: Path) -> Path | None:
    """
    Attempt chapter generation if enabled and MKV has no existing chapters.
    Returns the path to the generated OGM chapter file, or None.
    Safe to call from any code path — all errors are caught and logged.
    """
    if not file_ctx.chapters_enabled:
        return None
    if cfg.output_action != 'remux':
        return None
    if bool((mkv_info or {}).get('chapters')):
        logging.info("[Chapters] MKV ya tiene capítulos. Omitiendo generación.")
        return None
    # Filtro de ruta: solo generar capítulos si el MKV está bajo la ruta configurada
    chapters_anime_path = cfg.anime_path
    if chapters_anime_path:
        try:
            mkv_resolved = str(mkv_path.resolve())
            filter_resolved = str(Path(chapters_anime_path).resolve())
            if not mkv_resolved.startswith(filter_resolved):
                logging.info("[Chapters] MKV no está bajo chapters_anime_path ('%s'). Omitiendo.", chapters_anime_path)
                return None
        except Exception as e:
            logging.warning("[Chapters] Error verificando ruta anime_path: %s", e)

    # Verificar/instalar dependencias de capítulos antes de intentar
    from src.dependencies import check_and_install_chapter_deps
    if not check_and_install_chapter_deps():
        logging.warning("[Chapters] Dependencias de capítulos no disponibles. Omitiendo.")
        return None

    try:
        chapter_file_path = generate_chapters(
            file_ctx.series_title, mkv_path, tmpdir_path, cfg,
            season_number=file_ctx.season_number
        )
        if chapter_file_path:
            logging.info("[Chapters] Capítulos generados: %s", chapter_file_path.name)
        return chapter_file_path
    except Exception as e:
        logging.warning("[Chapters] Error en generación de capítulos (ignorado): %s", e)
        return None

def _embed_chapters_standalone(mkv_path: Path, chapter_file: Path, cfg, tool_paths: dict) -> bool:
    """Embed OGM chapters into an MKV file using mkvmerge (standalone, without reordering tracks)."""
    if not tool_paths or not tool_paths.get('mkvmerge'):
        logging.warning("[Chapters] mkvmerge no disponible para embedding standalone.")
        return False
    result = _remux_embed_chapters(mkv_path, chapter_file, cfg, tool_paths)
    for warn_msg in result.warnings:
        logging.warning("[Remux] %s", warn_msg)
    if result.ok:
        if cfg.replace_original_mkv:
            logging.info("[Chapters] Capítulos embebidos exitosamente (standalone).")
        else:
            logging.info("[Chapters] Guardado con capítulos como: %s", result.output.name)
    else:
        logging.warning("[Chapters] Error embedding capítulos standalone: %s",
                        '; '.join(result.warnings) or 'desconocido')
    return result.ok

def _detect_mode() -> str:
    """
    Detect execution mode based on environment variables.
    Returns: 'radarr', 'sonarr', or 'standalone'.
    Order: Radarr first (to prevent env var collision), then Sonarr, then Standalone.
    """
    if os.environ.get('radarr_eventtype'):
        return 'radarr'
    if any(os.environ.get(v) for v in ('sonarr_episodefile_path', 'sonarr_episodefile_paths', 'sonarr_filepath', 'sonarr_eventtype')):
        return 'sonarr'
    return 'standalone'

def _extract_contexts(mode: str) -> list[dict]:
    """
    Extract mkv_path, series_title, episode_title, season_number based on mode.
    Returns list of dicts with keys: mkv_path (Path), series_title (str), episode_title (str),
    season_number (int|None), chapters_override (bool|None).
    Raises SystemExit on invalid input.
    """
    contexts = []
    if mode == 'radarr':
        # --- Radarr Mode ---
        for key, value in os.environ.items():
            if key.lower().startswith('radarr_'):
                logging.debug(f"[Radarr Env] {key}={value}")

        event_type = os.environ.get('radarr_eventtype', '')
        if event_type.lower() == 'test':
            logging.info("Evento Radarr 'Test' recibido. Saliendo OK.")
            sys.exit(0)

        mkv_path_str = os.environ.get('radarr_moviefile_path')
        if not mkv_path_str:
            raise SystemExit("Radarr: Variable 'radarr_moviefile_path' no encontrada.")

        movie_title = os.environ.get('radarr_movie_title', 'Desconocido')
        contexts.append({
            'mkv_path': Path(mkv_path_str),
            'series_title': movie_title,
            'episode_title': movie_title,
            'season_number': None,
            'chapters_override': False,  # Radarr = NEVER chapters
        })

    elif mode == 'sonarr':
        # --- Sonarr Mode ---
        for key, value in os.environ.items():
            if key.lower().startswith('sonarr_'):
                logging.debug(f"[Sonarr Env] {key}={value}")

        event_type = os.environ.get('sonarr_eventtype', '')
        if event_type.lower() == 'test':
            logging.info("Evento Sonarr 'Test' recibido. Saliendo OK.")
            sys.exit(0)

        mkv_paths_raw = None
        env_source = None

        if 'sonarr_episodefile_path' in os.environ:
            mkv_paths_raw = os.environ.get('sonarr_episodefile_path')
            env_source = 'sonarr_episodefile_path'
        elif 'sonarr_episodefile_paths' in os.environ:
            mkv_paths_raw = os.environ.get('sonarr_episodefile_paths')
            env_source = 'sonarr_episodefile_paths'
        elif 'sonarr_filepath' in os.environ:
            mkv_paths_raw = os.environ.get('sonarr_filepath')
            env_source = 'sonarr_filepath'

        if not mkv_paths_raw:
            raise SystemExit("Sonarr: Variable con ruta de archivo no encontrada.")

        mkv_path_list = [p.strip() for p in mkv_paths_raw.split('|') if p.strip()]
        logging.info("Rutas recibidas (vía %s): %s", env_source, mkv_path_list)

        series_title = os.environ.get('sonarr_series_title', 'Desconocido')
        
        for path_str in mkv_path_list:
            episode_title = os.environ.get('sonarr_episodefile_episodetitles', '')
            if not episode_title:
                _ep_nums = os.environ.get('sonarr_episodefile_episodenumbers', '')
                if _ep_nums:
                    episode_title = f"Episodio {_ep_nums}"
                else:
                    # Fallback: extraer del nombre de archivo (S01E05 -> "Episodio 5")
                    _ep_match = re.search(r'[Ss]\d+[Ee](\d+)', path_str)
                    episode_title = f"Episodio {int(_ep_match.group(1))}" if _ep_match else 'Desconocido'

            season_number = None
            _raw_season = os.environ.get('sonarr_episodefile_seasonnumber')
            if _raw_season:
                try:
                    season_number = int(_raw_season)
                except (ValueError, TypeError):
                    logging.debug("sonarr_episodefile_seasonnumber no es entero: '%s'", _raw_season)

            if season_number is None:
                _season_match = re.search(r'[Ss](\d+)[Ee]', path_str)
                if _season_match:
                    season_number = int(_season_match.group(1))
                    logging.debug("Temporada extraída del path: %d", season_number)

            contexts.append({
                'mkv_path': Path(path_str),
                'series_title': series_title,
                'episode_title': episode_title,
                'season_number': season_number,
                'chapters_override': None,  # Sonarr: use config
            })

    else:
        # --- Standalone Mode ---
        import argparse
        parser = argparse.ArgumentParser(
            description='Traductor MKV - Modo Standalone'
        )
        parser.add_argument('--file', '-f', type=str, default=None,
                            help='Ruta al archivo MKV')
        parser.add_argument('--series', '-s', type=str, default=None,
                            help='Nombre de la serie (para prompt y búsqueda de temas)')
        parser.add_argument('--season', '-n', type=int, default=None,
                            help='Número de temporada (para selección de OP/ED)')
        args = parser.parse_args()

        mkv_path_str = args.file
        if not mkv_path_str:
            # Try tkinter file picker
            if TKINTER_AVAILABLE:
                try:
                    _root = tk.Tk()
                    _root.withdraw()
                    mkv_path_str = filedialog.askopenfilename(
                        title='Seleccionar archivo MKV',
                        filetypes=[('MKV files', '*.mkv'), ('All files', '*.*')]
                    )
                    _root.destroy()
                except Exception as e:
                    logging.warning("tkinter no disponible (sin display?): %s", e)

        if not mkv_path_str:
            raise SystemExit("Standalone: Proporcione --file /ruta/al/archivo.mkv")

        series_title = args.series or 'Desconocido'
        season_number = args.season

        if season_number is None and series_title == 'Desconocido':
            _season_match = re.search(r'[Ss](\d+)[Ee]', mkv_path_str)
            if _season_match:
                season_number = int(_season_match.group(1))

        contexts.append({
            'mkv_path': Path(mkv_path_str),
            'series_title': series_title,
            'episode_title': 'Desconocido',
            'season_number': season_number,
            'chapters_override': None,  # Standalone: use config
        })
    
    return contexts


def process_file(ctx: dict, cfg, tool_paths: dict, translation_cache: TranslationCache):
    """Procesa un único archivo MKV basado en el contexto proporcionado."""
    mkv_path = ctx['mkv_path']
    series_title = ctx['series_title']
    episode_title = ctx['episode_title']
    season_number = ctx['season_number']
    
    saved_subtitle_temp_path = None
    final_action_successful = False
    output_sub_ext = '.srt'

    # Contexto por archivo (inmutable). Radarr nunca genera capítulos.
    if ctx['chapters_override'] is False and cfg.chapters_enabled:
        logging.info("[Radarr] Capítulos deshabilitados para películas.")
    file_ctx = FileContext(
        mkv_path=mkv_path,
        series_title=series_title,
        episode_title=episode_title,
        season_number=season_number,
        chapters_enabled=cfg.chapters_enabled and ctx['chapters_override'] is not False,
    )

    try:
        # Validar archivo MKV
        if not mkv_path.is_file():
            logging.error("ERROR: Ruta no existe o no es archivo: %s", mkv_path)
            return
        if mkv_path.suffix.lower() != '.mkv':
            logging.error("ERROR: Archivo no es .mkv: %s", mkv_path.name)
            return
        try:
            with open(mkv_path, 'rb') as f:
                f.read(1)
            logging.info("Archivo MKV OK: %s", mkv_path.name)
        except Exception as e:
            logging.error("Error lectura MKV (Permisos? Corrupto?): %s", e, exc_info=True)
            return
        logging.debug("Validaciones de ruta y archivo OK.")

        logging.info("Procesando: Serie='%s', Temporada=%s, Episodio='%s'",
                     file_ctx.series_title, file_ctx.season_number, file_ctx.episode_title)

        mkv_info, track_codecs = phases.probe_mkv_info(mkv_path, tool_paths)

        mkv = phases.load_mkv_structure(mkv_path, tool_paths)
        if mkv is None:
            return

        subs_tracks, target_lang_found = phases.scan_tracks_for_target(mkv, cfg)

        if target_lang_found:
            if cfg.reorder_existing_tracks:
                logging.info("Pista objetivo encontrada. Iniciando proceso de reordenamiento inteligente...")
                # Generar capítulos antes de reordenar (si habilitado y MKV no tiene)
                with tempfile.TemporaryDirectory(prefix="chap_reorder_") as chap_tmpdir:
                    chapter_file = _try_generate_chapters(
                        file_ctx, mkv_path, mkv_info, cfg, Path(chap_tmpdir)
                    )
                    if mkv_info and reorder_tracks(mkv_path, mkv_info, cfg, tool_paths, chapter_file):
                        logging.info("Reordenamiento finalizado con éxito.")
                    else:
                        if mkv_info is None:
                            logging.warning("No se pudo obtener info MKV para reordenar. Omitiendo reordenamiento.")
                        else:
                            logging.warning("Reordenamiento fallido o innecesario.")
                        # Si reorder falló pero tenemos capítulos, intentar embedding standalone
                        if chapter_file and chapter_file.exists() and mkv_info:
                            logging.info("[Chapters] Reorder falló, intentando embedding de capítulos standalone...")
                            _embed_chapters_standalone(mkv_path, chapter_file, cfg, tool_paths)
                return
            else:
                # Pista existe pero reorder desactivado — aún intentar capítulos
                if file_ctx.chapters_enabled and cfg.output_action == 'remux':
                    with tempfile.TemporaryDirectory(prefix="chap_only_") as chap_tmpdir:
                        chapter_file = _try_generate_chapters(
                            file_ctx, mkv_path, mkv_info, cfg, Path(chap_tmpdir)
                        )
                        if chapter_file and chapter_file.exists():
                            _embed_chapters_standalone(mkv_path, chapter_file, cfg, tool_paths)
                logging.info("Pista objetivo ya existe y reordenamiento desactivado.")
                return
        
        if not subs_tracks:
            logging.warning("No hay subtítulos en %s", mkv_path.name)
            return

        # --- PASO 5: Seleccionar pista fuente ---
        codecs_to_use = track_codecs if track_codecs else {
            getattr(t, 'track_id', '?'): getattr(t, 'codec_id', '?') for t in mkv.tracks
        }
        src_track = select_subtitle_track(subs_tracks, codecs_to_use, cfg)
        if not src_track:
            logging.warning("No hay pista fuente válida en %s", mkv_path.name)
            return
        src_track_id = getattr(src_track, 'track_id', 'N/A')
        source_codec_id = codecs_to_use.get(src_track_id, '?')
        if source_codec_id == '?':
            source_codec_id = getattr(src_track, 'codec_id', '?')
        if source_codec_id != '?' and ('vobsub' in source_codec_id.lower() or 'pgs' in source_codec_id.lower()):
            logging.warning("Pista fuente es imagen en %s", mkv_path.name)
            return

        # --- PASO 6: Configurar API ---
        try:
            api_client = APIClient(cfg, file_ctx)
            logging.info("Conexión API OK.")
        except Exception as e:
            logging.exception("Error config/conexión API:")
            return

        # --- PASO 7: Confirmación ---
        lang_n = getattr(src_track, 'language', 'und')
        codec_n = source_codec_id or '?'
        logging.info("--- Iniciando Proceso ---")
        logging.info("Fuente: ID %s (Lang '%s', Codec '%s')", src_track_id, lang_n, codec_n)
        logging.info("Traduciendo a: %s usando '%s'", cfg.target_language_name, api_client.current_model_name)
        logging.info(f"Acción final: {cfg.output_action}")

        # --- PASO 8-12: Procesamiento principal ---
        with tempfile.TemporaryDirectory(prefix="subtrans_") as tmpdir:
          try:
            logging.debug(f"Temp dir: {tmpdir}")
            # --- PASO 7.5: Generación de Capítulos (paralelo con traducción) ---
            chapter_future = None
            chapter_file_path = None
            _chapter_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chapters")
            try:
                chapter_future = _chapter_executor.submit(
                    _try_generate_chapters,
                    file_ctx, mkv_path, mkv_info, cfg, Path(tmpdir)
                )
                logging.info("[Chapters] Generación iniciada en hilo paralelo.")
            except Exception as e:
                logging.warning("[Chapters] No se pudo iniciar hilo paralelo: %s. Ejecutando en hilo principal.", e)
                chapter_file_path = _try_generate_chapters(
                    file_ctx, mkv_path, mkv_info, cfg, Path(tmpdir)
                )
            tmp_sub_extracted = phases.extract_source_subtitle(
                mkv_path, src_track_id,
                get_subtitle_extension(source_codec_id), Path(tmpdir), tool_paths)
            subs, output_format = phases.load_subtitles(tmp_sub_extracted)
            output_sub_ext = '.' + output_format
            translated_sub_base_name = mkv_path.stem + f".{cfg.primary_target_code}"
            temp_sub_path_final = Path(tmpdir) / (translated_sub_base_name + output_sub_ext)
            logging.debug("Temp salida sub (%s): %s", output_format.upper(), temp_sub_path_final)
            # --- 10. Traducir ---
            if cfg.enable_translation_cache:
                logging.info("Caché HABILITADO.")
            lines_to_translate_original, line_indices_map, _originals_idx = \
                phases.collect_translatable(subs)
            num_proc = len(lines_to_translate_original)
            if num_proc == 0:
                logging.warning("Subtítulo sin texto traducible en %s", mkv_path.name)
                return
            logging.info("--- Traducción (%d líneas válidas, con fallback recursivo) ---", num_proc)
            t_start = time.time()
            
            stats = {'ok': 0, 'errors': 0, 'processed': num_proc}
            all_translated_results = phases.translate_lines(
                api_client, translation_cache, lines_to_translate_original)

            # --- Análisis Post-Traducción ---
            validator = TranslationValidator(cfg)
            validation_results = validator.validate_all(lines_to_translate_original, all_translated_results)
            
            corrected_count = 0
            issues_found = [r for r in validation_results if r.issues]
            if issues_found:
                corrector = TranslationCorrector(api_client, translation_cache)
                all_translated_results = corrector.attempt_corrections(issues_found, all_translated_results)
                corrected_count = len(issues_found)
            else:
                logging.info("Análisis completado: ¡No se detectaron problemas!")

            # --- Aplicar resultados finales a los subtítulos ---
            stats.update(phases.apply_results(subs, line_indices_map,
                                              all_translated_results))

            t_end = time.time()
            logging.info("--- Resumen Traducción: %d líneas en %.1fs | OK=%d | Errores=%d | Corregidas=%d/%d con issues ---",
                         num_proc, t_end - t_start, stats['ok'], stats['errors'],
                         corrected_count, len(validation_results))
            # --- Recoger resultado de capítulos (si se ejecutó en paralelo) ---
            if chapter_future is not None:
                try:
                    chapter_file_path = chapter_future.result(timeout=300)
                    if chapter_file_path:
                        logging.info("[Chapters] Generación paralela completada: %s", chapter_file_path.name)
                    else:
                        logging.debug("[Chapters] Generación paralela completada (sin capítulos).")
                except Exception as e:
                    logging.warning("[Chapters] Error en generación paralela de capítulos: %s", e)
                    chapter_file_path = None
                finally:
                    _chapter_executor.shutdown(wait=False)
            # --- 11. Guardar Subtítulo Temporal ---
            if stats['ok'] > 0:
                logging.info("--- Guardado Temporal Subtítulo ---")
                try:
                    subs.save(str(temp_sub_path_final), format=output_format, encoding='utf-8-sig')
                    logging.info("Guardado OK!")
                    saved_subtitle_temp_path = temp_sub_path_final
                except Exception as e:
                    logging.exception("Error guardando sub temp:")
            else:
                logging.warning("No se guardó sub temp (0 OK).")

            # --- 12. Acción Final (remux vía src/remux.py) ---
            if saved_subtitle_temp_path and saved_subtitle_temp_path.exists():
                output_action = cfg.output_action
                logging.info(f"--- Acción Final: {output_action} ---")
                if output_action == 'remux':
                    if tool_paths and tool_paths.get('mkvmerge'):
                        logging.info(f"MKV Original: {mkv_path.name}, Sub: {saved_subtitle_temp_path.name}")
                        result = embed_translation(
                            mkv_path, saved_subtitle_temp_path, cfg, tool_paths,
                            chapters=chapter_file_path,
                        )
                        for warn_msg in result.warnings:
                            logging.warning("[Remux] %s", warn_msg)
                        final_action_successful = result.ok
                        if result.ok:
                            logging.info("Muxing OK -> %s", result.output.name)
                    else:
                        logging.error("mkvmerge no encontrado para 'remux'.")
                elif output_action == 'save_separate_sub':
                    final_action_successful = True
            elif not saved_subtitle_temp_path:
                logging.warning("No se generó sub temp, no hay acción final.")
            # --- FIN Acción Final ---

          except SystemExit as e:
              exit_msg = str(e) if str(e) not in ['None', '0'] else "Salida controlada."
              logging.warning(f"Proceso detenido: {exit_msg}")
          except Exception as e:
              logging.exception("Error crítico procesamiento principal:")
          finally:
            # Asegurar shutdown del executor de capítulos
            if '_chapter_executor' in locals():
                try:
                    if 'chapter_future' in locals() and chapter_future is not None and not chapter_future.done():
                        chapter_future.cancel()
                    _chapter_executor.shutdown(wait=True, cancel_futures=True)
                except Exception:
                    pass
            # --- Limpieza Final ---
            logging.debug("Iniciando limpieza final...")
            action_to_perform = cfg.output_action
            if saved_subtitle_temp_path and saved_subtitle_temp_path.exists():
                if action_to_perform == 'remux':
                    if final_action_successful:
                        try:
                            os.remove(saved_subtitle_temp_path)
                            logging.info("Subtítulo temporal eliminado (en MKV).")
                        except OSError as e:
                            logging.warning(f"No se pudo eliminar el subtítulo temporal {saved_subtitle_temp_path}: {e}")
                    else:
                        final_sub_path = mkv_path.parent / saved_subtitle_temp_path.name
                        logging.warning("Muxing fallido. Guardando sub respaldo.")
                        try:
                            if final_sub_path.exists():
                                logging.warning(f"'{final_sub_path.name}' existe. Sobrescribiendo.")
                                os.remove(final_sub_path)
                            shutil.move(str(saved_subtitle_temp_path), final_sub_path)
                            logging.info(f"Sub respaldo movido a: {final_sub_path}")
                        except Exception as e:
                            logging.exception(f"No se pudo mover sub respaldo a {final_sub_path}.")
                elif action_to_perform == 'save_separate_sub':
                    final_sub_path = mkv_path.parent / saved_subtitle_temp_path.name
                    logging.info(f"Moviendo sub a destino final: {final_sub_path}")
                    try:
                        if final_sub_path.exists():
                            logging.warning(f"'{final_sub_path.name}' existe. Sobrescribiendo.")
                            os.remove(final_sub_path)
                        shutil.move(str(saved_subtitle_temp_path), final_sub_path)
                        logging.info(f"Sub guardado como: {final_sub_path}")
                    except Exception as e:
                        logging.exception(f"No se pudo mover sub final a {final_sub_path}.")
                        final_action_successful = False # Marcar fallo si movimiento falla
            logging.debug("Directorio temporal será limpiado automáticamente.")
            # --- FIN Limpieza Final ---
    except SystemExit as e:
        exit_msg = str(e) if str(e) not in ['None', '0'] else "Salida controlada."
        logging.warning(f"Ejecución abortada para {mkv_path.name}: {exit_msg}")
    except Exception as e:
        logging.exception(f"Error fatal no recuperado en {mkv_path.name}:")

def main():

    """Punto de entrada: config, updater, herramientas y bucle de contextos."""
    setup_logging()
    from src.__version__ import __version__ as script_version
    
    # Determinar rutas absolutas para compatibilidad con Sonarr
    script_dir = Path(__file__).parent.resolve()
    config_path = script_dir / "config.ini"
    
    logging.info(f"--- Traductor MKV para Sonarr/Radarr ({script_version}) ---")
    logging.debug(f"Directorio del script: {script_dir}")
    logging.debug(f"Directorio de trabajo actual (CWD): {Path.cwd()}")
    logging.debug(f"Ruta config.ini: {config_path}")
    

    cfg, config_warnings = load_config(config_path, capture_warnings=True)
    if config_warnings:
        logging.info("Avisos de configuración: %d (detalle en el log)", len(config_warnings))

    # Reconfigurar logging ahora que conocemos debug_translation
    # (el file handler sube a DEBUG y se activa la traza por etapa)
    if cfg.debug_translation:
        setup_logging(debug_mode=True)
        logging.info("Modo DEBUG_TRANSLATION activo: traza completa en el archivo de log.")

    # --- Auto-Updater ---
    if cfg.auto_update:
        try:
            import src.updater as updater
            updater.clean_old_files(script_dir)
            updater.check_and_update(script_version, script_dir)
        except Exception as e:
            logging.warning(f"Error en auto-updater: {e}")


    tool_paths = check_mkvtoolnix_tools(cfg)
    if cfg.output_action == 'remux' and (not tool_paths or not tool_paths.get('mkvmerge') or not tool_paths.get('mkvextract')): sys.exit("MKVToolNix necesario para 'remux' no encontrado.")
    elif not tool_paths or not tool_paths.get('mkvextract'): sys.exit("mkvextract necesario no encontrado.")
    if cfg.api_key == "TU_API_KEY_AQUI": sys.exit("Clave API no configurada.")

    translation_cache = TranslationCache(cfg.enable_translation_cache)

    def _sigterm_handler(signum, frame):
        """Guarda la caché y re-envía SIGTERM con handler default (muere de verdad)."""
        logging.warning("SIGTERM recibido. Guardando caché antes de salir...")
        try:
            translation_cache.save_cache()
        except Exception:
            logging.exception("No se pudo guardar la caché en SIGTERM")
        # sys.exit(0) aquí elevaba SystemExit que process_file tragaba:
        # el batch seguía y el proceso terminaba 0 pese al kill.
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)
    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        # --- PASO 1: Detectar modo y obtener contextos ---
        mode = _detect_mode()
        logging.info("Modo detectado: %s", mode.upper())
        contexts = _extract_contexts(mode)

        for ctx_idx, ctx in enumerate(contexts):
            logging.info(f"--- Procesando archivo {ctx_idx + 1}/{len(contexts)} ---")
            process_file(ctx, cfg, tool_paths, translation_cache)

    except SystemExit as e:
        exit_msg = str(e) if str(e) not in ['None', '0'] else "Salida controlada."
        logging.warning(f"Ejecución abortada: {exit_msg}")
    except Exception as e:
        logging.exception("Error fatal no recuperado:")
    finally:
        if 'translation_cache' in locals() and cfg.enable_translation_cache: translation_cache.save_cache()
        else: logging.debug("No se guardó caché.")

        # Prune theme cache at the end of the batch
        if cfg.chapters_enabled and cfg.theme_cache_dir and cfg.theme_cache_dir.exists():
            from src.chapter_generator import prune_theme_cache
            prune_theme_cache(
                cfg.theme_cache_dir,
                cfg.max_theme_cache_mb,
                cfg.theme_cache_ttl_days
            )
    logging.info("--- Proceso Finalizado ---")

if __name__ == "__main__":
    # setup_logging() # Se llama dos veces, main() ya lo hace. Comentado para evitar duplicados.
    main()
