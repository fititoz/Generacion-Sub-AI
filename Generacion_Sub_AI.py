#!/usr/bin/env python3
# Script Version: 2026.03
"""
Generacion_Sub_AI — MKV subtitle translator with anime chapter generation.

Entry point for Sonarr, Radarr, and standalone execution modes.
Coordinates subtitle extraction, Gemini AI translation, post-translation
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
from src.constants import TAG_REGEX, CACHE_DIR_NAME, CACHE_FILE_NAME, LOG_FILENAME, PLACEHOLDER_PREFIX, PLACEHOLDER_SUFFIX, REQUIRED_PACKAGES
import re
import importlib.metadata

# --- Shim de compatibilidad: pkg_resources (setuptools >= 82.0.0 lo removió) ---
if 'pkg_resources' not in sys.modules:
    import types as _types
    import importlib.resources
    _pkg_resources = _types.ModuleType('pkg_resources')
    _pkg_resources.__package__ = 'pkg_resources'
    class _DistributionNotFound(Exception):
        pass
    _pkg_resources.DistributionNotFound = _DistributionNotFound
    def _get_distribution(name):
        try:
            return importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            raise _DistributionNotFound(name)
    _pkg_resources.get_distribution = _get_distribution
    def _resource_filename(package_name, resource_path):
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

from src.gemini_client import GeminiClient

# --- Configuración de Logging ---
from src.logging_setup import setup_logging

# --- Carga de Configuración ---
from src.config_manager import ConfigManager

from src.cache_manager import TranslationCache
from src.translation_validator import TranslationValidator, TranslationCorrector
from src.exceptions import SubtitleTranslationError, MKVOperationError, SubtitleParsingError
from src.chapter_generator import generate_chapters
from src.track_reorder import reorder_tracks

# --- Funciones Auxiliares ---
def find_executable(name, provided_path=None):
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
def check_mkvtoolnix_tools(config):
    mkvtoolnix_dir = config.get('MKVTOOLNIX_DIR')
    mkvmerge_path = None
    mkvextract_path = None
    if mkvtoolnix_dir:
        mkvmerge_path = Path(mkvtoolnix_dir) / ('mkvmerge.exe' if os.name == 'nt' else 'mkvmerge')
        mkvextract_path = Path(mkvtoolnix_dir) / ('mkvextract.exe' if os.name == 'nt' else 'mkvextract')
    logging.debug("Verificando MKVToolNix...")
    mkvmerge = find_executable('mkvmerge', str(mkvmerge_path) if mkvmerge_path else None)
    mkvextract = find_executable('mkvextract', str(mkvextract_path) if mkvextract_path else None)
    mkvextract_needed = True
    mkvmerge_needed = config.get('OUTPUT_ACTION', 'remux') == 'remux'
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
def select_subtitle_track(tracks, track_codecs, config):
    candidates = []
    image_subs_found = []
    preferred_lang = config['PREFERRED_SOURCE_LANG']
    target_codes_set = config['TARGET_LANGUAGE_CODES_SET']
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
            elif lang not in target_codes_set or lang == 'und':
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
    non_und = [t for t in candidates if (getattr(t, 'language', 'und') or 'und') != 'und']
    if non_und:
        first = non_und[0]
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
# (Las funciones de traducción ahora se gestionan a través de la clase GeminiClient en src/gemini_client.py)



def _try_generate_chapters(series_title: str, mkv_path: Path, mkv_info: dict, config: dict, tmpdir_path: Path, *, season_number: int | None = None) -> Path | None:
    """
    Attempt chapter generation if enabled and MKV has no existing chapters.
    Returns the path to the generated OGM chapter file, or None.
    Safe to call from any code path — all errors are caught and logged.
    """
    if not config.get('CHAPTERS_ENABLED'):
        return None
    if config.get('OUTPUT_ACTION') != 'remux':
        return None
    if bool((mkv_info or {}).get('chapters')):
        logging.info("[Chapters] MKV ya tiene capítulos. Omitiendo generación.")
        return None
    # Filtro de ruta: solo generar capítulos si el MKV está bajo la ruta configurada
    chapters_anime_path = config.get('CHAPTERS_ANIME_PATH')
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
            series_title, mkv_path, tmpdir_path, config,
            season_number=season_number
        )
        if chapter_file_path:
            logging.info("[Chapters] Capítulos generados: %s", chapter_file_path.name)
        return chapter_file_path
    except Exception as e:
        logging.warning("[Chapters] Error en generación de capítulos (ignorado): %s", e)
        return None

def _embed_chapters_standalone(mkv_path: Path, chapter_file: Path, config: dict, tool_paths: dict) -> bool:
    """Embed OGM chapters into an MKV file using mkvmerge (standalone, without reordering tracks)."""
    if not tool_paths or not tool_paths.get('mkvmerge'):
        logging.warning("[Chapters] mkvmerge no disponible para embedding standalone.")
        return False
    temp_chap_out = mkv_path.with_suffix('.chapters_temp.mkv')
    try:
        chap_cmd = [
            tool_paths['mkvmerge'],
            '-o', str(temp_chap_out),
            '--chapters', str(chapter_file),
            str(mkv_path),
        ]
        subprocess.run(chap_cmd, check=True, capture_output=True, text=True,
                        encoding='utf-8', errors='replace', timeout=300)
        if config.get('REPLACE_ORIGINAL_MKV', True):
            os.replace(temp_chap_out, mkv_path)
            logging.info("[Chapters] Capítulos embebidos exitosamente (standalone).")
        else:
            final_name = mkv_path.with_stem(mkv_path.stem + ".chapters")
            os.replace(temp_chap_out, final_name)
            logging.info("[Chapters] Guardado con capítulos como: %s", final_name.name)
        return True
    except Exception as e:
        logging.warning("[Chapters] Error embedding capítulos standalone: %s", e)
        if temp_chap_out.exists():
            os.remove(temp_chap_out)
        return False

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

def _extract_context(mode: str) -> dict:
    """
    Extract mkv_path, series_title, episode_title, season_number based on mode.
    Returns dict with keys: mkv_path (Path), series_title (str), episode_title (str),
    season_number (int|None), chapters_override (bool|None).
    Raises SystemExit on invalid input.
    """
    if mode == 'radarr':
        # --- Radarr Mode ---
        event_type = os.environ.get('radarr_eventtype', '')
        if event_type.lower() == 'test':
            logging.info("Evento Radarr 'Test' recibido. Saliendo OK.")
            sys.exit(0)

        mkv_path_str = os.environ.get('radarr_moviefile_path')
        if not mkv_path_str:
            raise SystemExit("Radarr: Variable 'radarr_moviefile_path' no encontrada.")

        movie_title = os.environ.get('radarr_movie_title', 'Desconocido')
        return {
            'mkv_path': Path(mkv_path_str),
            'series_title': movie_title,
            'episode_title': movie_title,
            'season_number': None,
            'chapters_override': False,  # Radarr = NEVER chapters
        }

    elif mode == 'sonarr':
        # --- Sonarr Mode ---
        event_type = os.environ.get('sonarr_eventtype', '')
        if event_type.lower() == 'test':
            logging.info("Evento Sonarr 'Test' recibido. Saliendo OK.")
            sys.exit(0)

        mkv_path_str = None
        env_source = None

        if 'sonarr_episodefile_path' in os.environ:
            mkv_path_str = os.environ.get('sonarr_episodefile_path')
            env_source = 'sonarr_episodefile_path'
        elif 'sonarr_episodefile_paths' in os.environ:
            mkv_path_str = os.environ.get('sonarr_episodefile_paths')
            env_source = 'sonarr_episodefile_paths'
        elif 'sonarr_filepath' in os.environ:
            mkv_path_str = os.environ.get('sonarr_filepath')
            env_source = 'sonarr_filepath'

        if mkv_path_str and '|' in mkv_path_str:
            mkv_path_str = mkv_path_str.split('|')[0]
            logging.warning("Múltiples rutas, usando primera: '%s'", mkv_path_str)

        if not mkv_path_str:
            raise SystemExit("Sonarr: Variable con ruta de archivo no encontrada.")

        logging.info("Ruta recibida (vía %s): %s", env_source, mkv_path_str)

        series_title = os.environ.get('sonarr_series_title', 'Desconocido')
        episode_title = os.environ.get('sonarr_episodefile_episodetitles', '')
        if not episode_title:
            _ep_nums = os.environ.get('sonarr_episodefile_episodenumbers', '')
            if _ep_nums:
                episode_title = f"Episodio {_ep_nums}"
            else:
                # Fallback: extraer del nombre de archivo (S01E05 -> "Episodio 5")
                _ep_match = re.search(r'[Ss]\d+[Ee](\d+)', mkv_path_str)
                episode_title = f"Episodio {int(_ep_match.group(1))}" if _ep_match else 'Desconocido'

        season_number = None
        _raw_season = os.environ.get('sonarr_episodefile_seasonnumber')
        if _raw_season:
            try:
                season_number = int(_raw_season)
            except (ValueError, TypeError):
                logging.debug("sonarr_episodefile_seasonnumber no es entero: '%s'", _raw_season)

        if season_number is None:
            _season_match = re.search(r'[Ss](\d+)[Ee]', mkv_path_str)
            if _season_match:
                season_number = int(_season_match.group(1))
                logging.debug("Temporada extraída del path: %d", season_number)

        return {
            'mkv_path': Path(mkv_path_str),
            'series_title': series_title,
            'episode_title': episode_title,
            'season_number': season_number,
            'chapters_override': None,  # Sonarr: use config
        }

    else:
        # --- Standalone Mode ---
        import argparse
        parser = argparse.ArgumentParser(
            description='Traductor MKV - Modo Standalone'
        )
        parser.add_argument('--file', '-f', type=str, default=None,
                            help='Ruta al archivo MKV')
        parser.add_argument('--series', '-s', type=str, default=None,
                            help='Nombre de la serie (para prompt Gemini y búsqueda de temas)')
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

        return {
            'mkv_path': Path(mkv_path_str),
            'series_title': series_title,
            'episode_title': 'Desconocido',
            'season_number': season_number,
            'chapters_override': None,  # Standalone: use config
        }

def main():
    setup_logging()
    from src.__version__ import __version__ as script_version
    
    # Determinar rutas absolutas para compatibilidad con Sonarr
    script_dir = Path(__file__).parent.resolve()
    config_path = script_dir / "config.ini"
    
    logging.info(f"--- Traductor MKV para Sonarr/Radarr ({script_version}) ---")
    logging.debug(f"Directorio del script: {script_dir}")
    logging.debug(f"Directorio de trabajo actual (CWD): {Path.cwd()}")
    logging.debug(f"Ruta config.ini: {config_path}")
    
    config_manager = ConfigManager(config_path)
    config = config_manager.get_all()

    tool_paths = check_mkvtoolnix_tools(config)
    if config['OUTPUT_ACTION'] == 'remux' and (not tool_paths or not tool_paths.get('mkvmerge') or not tool_paths.get('mkvextract')): sys.exit("MKVToolNix necesario para 'remux' no encontrado.")
    elif not tool_paths or not tool_paths.get('mkvextract'): sys.exit("mkvextract necesario no encontrado.")
    if config['GEMINI_API_KEY'] == "TU_API_KEY_AQUI": sys.exit("Clave API Gemini no configurada.")

    translation_cache = TranslationCache(config.get('ENABLE_TRANSLATION_CACHE', True))

    def _sigterm_handler(signum, frame):
        logging.warning("SIGTERM recibido. Guardando caché antes de salir...")
        if 'translation_cache' in locals() and config.get('ENABLE_TRANSLATION_CACHE'):
            translation_cache.save_cache()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sigterm_handler)

    mkv_path = None
    saved_subtitle_temp_path = None
    final_action_successful = False
    output_sub_ext = '.srt'

    try:
        # --- PASO 1: Detectar modo y obtener contexto ---
        mode = _detect_mode()
        logging.info("Modo detectado: %s", mode.upper())
        ctx = _extract_context(mode)

        mkv_path = ctx['mkv_path']
        series_title = ctx['series_title']
        episode_title = ctx['episode_title']
        season_number = ctx['season_number']

        # Radarr override: deshabilitar capítulos
        if ctx['chapters_override'] is False:
            config['CHAPTERS_ENABLED'] = False
            logging.info("[Radarr] Capítulos deshabilitados para películas.")

        # Validar archivo MKV
        if not mkv_path.is_file():
            logging.error("ERROR: Ruta no existe o no es archivo: %s", mkv_path)
            raise SystemExit(f"Ruta inválida: {mkv_path}")
        if mkv_path.suffix.lower() != '.mkv':
            logging.error("ERROR: Archivo no es .mkv: %s", mkv_path.name)
            raise SystemExit(f"Archivo no es MKV: {mkv_path.name}")
        try:
            with open(mkv_path, 'rb') as f:
                f.read(1)
            logging.info("Archivo MKV OK: %s", mkv_path.name)
        except Exception as e:
            logging.error("Error lectura MKV (Permisos? Corrupto?): %s", e, exc_info=True)
            raise SystemExit("Error lectura archivo.")
        logging.debug("Validaciones de ruta y archivo OK.")

        # Inyectar contexto en config
        config['SEASON_NUMBER'] = season_number
        config['SERIES_TITLE'] = series_title
        config['EPISODE_TITLE'] = episode_title
        logging.info("Contexto %s: Serie='%s', Temporada=%s, Episodio='%s'",
                     mode.capitalize(), series_title, season_number, episode_title)

        # --- PASO 2: Obtener info detallada ---
        track_codecs = {}
        mkv_info = None
        if tool_paths and tool_paths.get('mkvmerge'):
            try:
                logging.info("Obteniendo info detallada (mkvmerge -J)...")
                mkvmerge_cmd = [tool_paths['mkvmerge'], '-J', str(mkv_path)]
                result = subprocess.run(mkvmerge_cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace', timeout=60)
                mkv_info = json.loads(result.stdout)
                logging.debug("JSON parseado.")
                if 'tracks' in mkv_info:
                    for track_data in mkv_info['tracks']:
                        tid = track_data.get('id')
                        props = track_data.get('properties', {})
                        codec_id = props.get('codec_id')
                        if tid is not None and codec_id:
                            track_codecs[tid] = codec_id
                            logging.debug("  -> ID %d: Codec '%s'.", tid, codec_id)
            except subprocess.TimeoutExpired:
                logging.warning("Timeout obteniendo info detallada.")
            except subprocess.CalledProcessError as e: logging.warning(f"mkvmerge -J falló (código {e.returncode}): {e.stderr or e.stdout or 'Sin salida'}")
            except json.JSONDecodeError as e: logging.warning(f"Error decodificando JSON: {e}"); logging.debug("Output mkvmerge:\n%s", result.stdout if 'result' in locals() else "N/A")
            except Exception as e: logging.warning(f"Fallo inesperado info detallada: {e}", exc_info=False)
        else: logging.info("mkvmerge no disponible.")

        # --- PASO 3: Análisis pymkv ---
        logging.info("Analizando estructura pymkv: %s...", mkv_path.name)
        try:
            mkv = MKVFile(str(mkv_path))
            if tool_paths and tool_paths.get('mkvmerge'):
                try:
                    mkv.mkvmerge_path = tool_paths['mkvmerge']
                except Exception as e_assign:
                    logging.warning(f"No se pudo asignar mkvmerge_path a pymkv: {e_assign}")
            logging.info("Análisis pymkv OK.")
        except Exception as e: logging.exception("Error fatal análisis pymkv:"); raise SystemExit("Fallo análisis MKV.")

        # --- PASO 4: Comprobar pista objetivo ---
        subs_tracks = []
        target_lang_found = False
        logging.info("Pistas encontradas:")
        target_codes_set = config['TARGET_LANGUAGE_CODES_SET']
        target_name_display = config['TARGET_LANGUAGE_NAME']
        latino_kws = config['LATINO_KEYWORDS']
        spain_kws = config['SPAIN_KEYWORDS']
        target_is_latino = any(kw in target_name_display.lower() for kw in latino_kws)
        target_is_spain = any(kw in target_name_display.lower() for kw in spain_kws)

        for i, track in enumerate(mkv.tracks):
            tid = getattr(track, 'track_id', '?')
            ttype = getattr(track, 'track_type', '?')
            lang = getattr(track, 'language', 'und')
            codec_id = getattr(track, 'codec_id', '?')
            name = getattr(track, 'track_name', '?')
            default = getattr(track, 'default_track', False)
            forced = getattr(track, 'forced_track', False)

            codec = track_codecs.get(tid, getattr(track, 'codec_id', '?'))
            lang = lang if lang else 'und'
            track_name_lower = (name or '').lower()

            details = (
                f"  - Pista {i}: ID={tid}, T='{ttype}', L='{lang}', C='{codec}'"
                f"{f', N=\'{name}\'' if name else ''}"
                f"{' (Def)' if default else ''}"
                f"{' (Forz)' if forced else ''}"
            )
            logging.info(details)

            if ttype == 'subtitles':
                subs_tracks.append(track)
                is_target_variant = False
                if lang in target_codes_set:
                    is_target_variant = True
                    logging.info(f" --> Coincide código idioma ({lang}).")
                elif lang in ['spa', 'es'] and (target_is_latino or target_is_spain):
                    track_is_latino = any(kw in track_name_lower for kw in latino_kws)
                    track_is_spain = any(kw in track_name_lower for kw in spain_kws)
                    if target_is_latino and track_is_latino:
                        is_target_variant = True
                        logging.info(f" -> Coincide variante Latino ('{name}').")
                    elif target_is_spain and track_is_spain:
                        is_target_variant = True
                        logging.info(f" -> Coincide variante España ('{name}').")
                if is_target_variant:
                    logging.info(" --> ¡Encontrada pista objetivo!")
                    target_lang_found = True
        
        if target_lang_found:
            if config.get('REORDER_EXISTING_TRACKS', False):
                logging.info("Pista objetivo encontrada. Iniciando proceso de reordenamiento inteligente...")
                # Generar capítulos antes de reordenar (si habilitado y MKV no tiene)
                with tempfile.TemporaryDirectory(prefix="chap_reorder_") as chap_tmpdir:
                    chapter_file = _try_generate_chapters(
                        series_title, mkv_path, mkv_info, config, Path(chap_tmpdir),
                        season_number=config.get('SEASON_NUMBER')
                    )
                    if mkv_info and reorder_tracks(mkv_path, mkv_info, config, tool_paths, chapter_file):
                        logging.info("Reordenamiento finalizado con éxito.")
                    else:
                        if mkv_info is None:
                            logging.warning("No se pudo obtener info MKV para reordenar. Omitiendo reordenamiento.")
                        else:
                            logging.warning("Reordenamiento fallido o innecesario.")
                        # Si reorder falló pero tenemos capítulos, intentar embedding standalone
                        if chapter_file and chapter_file.exists() and mkv_info:
                            logging.info("[Chapters] Reorder falló, intentando embedding de capítulos standalone...")
                            _embed_chapters_standalone(mkv_path, chapter_file, config, tool_paths)
                sys.exit(0)
            else:
                # Pista existe pero reorder desactivado — aún intentar capítulos
                if config.get('CHAPTERS_ENABLED') and config.get('OUTPUT_ACTION') == 'remux':
                    with tempfile.TemporaryDirectory(prefix="chap_only_") as chap_tmpdir:
                        chapter_file = _try_generate_chapters(
                            series_title, mkv_path, mkv_info, config, Path(chap_tmpdir),
                            season_number=config.get('SEASON_NUMBER')
                        )
                        if chapter_file and chapter_file.exists():
                            _embed_chapters_standalone(mkv_path, chapter_file, config, tool_paths)
                raise SystemExit("Pista objetivo ya existe y reordenamiento desactivado.")
        
        if not subs_tracks: raise SystemExit("No hay subtítulos.")

        # --- PASO 5: Seleccionar pista fuente ---
        codecs_to_use = track_codecs if track_codecs else {
            getattr(t, 'track_id', '?'): getattr(t, 'codec_id', '?') for t in mkv.tracks
        }
        src_track = select_subtitle_track(subs_tracks, codecs_to_use, config)
        if not src_track:
            raise SystemExit("No hay pista fuente válida.")
        src_track_id = getattr(src_track, 'track_id', 'N/A')
        source_codec_id = codecs_to_use.get(src_track_id, '?')
        if source_codec_id == '?':
            source_codec_id = getattr(src_track, 'codec_id', '?')
        if source_codec_id != '?' and ('vobsub' in source_codec_id.lower() or 'pgs' in source_codec_id.lower()):
            raise SystemExit("Pista fuente es imagen.")

        # --- PASO 6: Configurar Gemini ---
        try:
            gemini_client = GeminiClient(config['GEMINI_API_KEY'], config)
            logging.info("Conexión Gemini OK.")
        except Exception as e: 
            logging.exception("Error config/conexión Gemini:")
            raise SystemExit(f"Fallo conexión Gemini: {e}")

        # --- PASO 7: Confirmación ---
        lang_n = getattr(src_track, 'language', 'und')
        codec_n = source_codec_id or '?'
        logging.info("--- Iniciando Proceso ---")
        logging.info("Fuente: ID %s (Lang '%s', Codec '%s')", src_track_id, lang_n, codec_n)
        logging.info("Traduciendo a: %s usando '%s'", config['TARGET_LANGUAGE_NAME'], gemini_client.current_model_name)
        logging.info(f"Acción final: {config['OUTPUT_ACTION']}")

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
                    series_title, mkv_path, mkv_info, config, Path(tmpdir),
                    season_number=config.get('SEASON_NUMBER')
                )
                logging.info("[Chapters] Generación iniciada en hilo paralelo.")
            except Exception as e:
                logging.warning("[Chapters] No se pudo iniciar hilo paralelo: %s. Ejecutando en hilo principal.", e)
                chapter_file_path = _try_generate_chapters(
                    series_title, mkv_path, mkv_info, config, Path(tmpdir),
                    season_number=config.get('SEASON_NUMBER')
                )
            # --- 8. Extraer Sub Fuente ---
            logging.info("--- Extracción Sub Fuente ---")
            source_sub_ext = get_subtitle_extension(source_codec_id)
            tmp_sub_extracted = Path(tmpdir) / f"track_{src_track_id}_source{source_sub_ext}"
            cmd_extract = [tool_paths['mkvextract'], str(mkv_path), 'tracks', f'{src_track_id}:{str(tmp_sub_extracted)}']
            logging.info("Ejecutando mkvextract...")
            try:
                proc_extract = subprocess.run(cmd_extract, check=True, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
                logging.info("Extracción OK.")
            except Exception as e:
                logging.exception("Fallo extracción mkvextract")
                raise
            if not tmp_sub_extracted.exists() or tmp_sub_extracted.stat().st_size == 0:
                raise Exception("Extracted file empty/missing")
            # --- 9. Cargar Subs ---
            logging.info("--- Carga Subs ---")
            loaded = False
            subs = None
            best_enc = 'utf-8'
            encs = ['utf-8', 'utf-8-sig', 'utf-16', 'latin-1', 'cp1252']
            for enc in encs:
                 try:
                     subs = pysubs2.load(str(tmp_sub_extracted), encoding=enc)
                     best_enc = enc
                     logging.info("Cargado OK (enc '%s', fmt '%s').", enc, subs.format)
                     loaded = True
                     break
                 except Exception:
                     pass
            if not loaded or subs is None:
                raise Exception("Subtitle load failed")
            # --- Determinar formato salida y path temporal final ---
            output_format = 'ass' if subs.format == 'ass' else 'srt'
            output_sub_ext = '.' + output_format
            translated_sub_base_name = mkv_path.stem + f".{config['PRIMARY_TARGET_CODE']}"
            temp_sub_path_final = Path(tmpdir) / (translated_sub_base_name + output_sub_ext)
            logging.debug("Temp salida sub (%s): %s", output_format.upper(), temp_sub_path_final)
            # --- 10. Traducir ---
            if config['ENABLE_TRANSLATION_CACHE']:
                logging.info("Caché HABILITADO.")
            lines_to_translate_original = []
            line_indices_map = {}
            original_subs_indices = []
            for i, line in enumerate(subs):
                if not line.is_comment and line.text.strip():
                    line_indices_map[len(lines_to_translate_original)] = i
                    lines_to_translate_original.append(line.text)
                    original_subs_indices.append(i)
            num_proc = len(lines_to_translate_original)
            if num_proc == 0: raise SystemExit("Subtítulo sin texto traducible.")
            logging.info("--- Traducción (%d líneas válidas, con fallback recursivo) ---", num_proc)
            logging.info("Modelo inicial: '%s'. Delay API: %.1fs.", gemini_client.current_model_name, config['API_CALL_DELAY'])
            t_start = time.time()
            
            stats = {'ok':0, 'errors': 0, 'processed': num_proc}
            try:
                all_translated_results = gemini_client.translate_recursive_fallback(lines_to_translate_original, translation_cache)
            except Exception as e:
                logging.error(f"Fallo crítico en traducción recursiva: {e}")
                all_translated_results = ["[[ERROR_FATAL_TRADUCTOR]]"] * num_proc

            t_end = time.time()
            logging.info("--- Resumen Traducción ---")
            logging.info("Completada en %.2fs. Líneas procesadas: %d/%d", t_end - t_start, stats['processed'], num_proc)

            # --- NUEVO: Análisis Post-Traducción ---
            logging.info("--- Análisis Post-Traducción ---")
            validator = TranslationValidator(config)
            validation_results = validator.validate_all(lines_to_translate_original, all_translated_results)
            
            issues_found = [r for r in validation_results if r.issues]
            if issues_found:
                corrector = TranslationCorrector(gemini_client, translation_cache)
                all_translated_results = corrector.attempt_corrections(issues_found, all_translated_results)
            else:
                logging.info("Análisis completado: ¡No se detectaron problemas!")

            # --- Aplicar resultados finales a los subtítulos ---
            for i, final_translated_text in enumerate(all_translated_results):
                original_subs_index = line_indices_map[i]
                if not final_translated_text.startswith("[["): 
                    subs[original_subs_index].text = final_translated_text.replace('\n','\\N')
                    stats['ok'] += 1
                else: 
                    stats['errors'] += 1
                    logging.warning("Error persistente línea [%d]: %s", original_subs_index, final_translated_text)

            logging.info("  Resultados Finales: OK=%d | Errores=%d", stats['ok'], stats['errors'])
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

            # --- 12. Acción Final (CORREGIDO mkvmerge command) ---
            if saved_subtitle_temp_path and saved_subtitle_temp_path.exists():
                output_action = config['OUTPUT_ACTION']
                logging.info(f"--- Acción Final: {output_action} ---")
                if output_action == 'remux':
                    if tool_paths and tool_paths.get('mkvmerge'):
                        should_replace = config['REPLACE_ORIGINAL_MKV']
                        mux_output_temp_path = mkv_path.with_suffix(mkv_path.suffix + ".muxing_temp") if should_replace else mkv_path.with_stem(mkv_path.stem + config['OUTPUT_MKV_SUFFIX'])
                        final_output_mkv_path = mkv_path if should_replace else mux_output_temp_path
                        logging.info(f"MKV Original: {mkv_path.name}, Sub: {saved_subtitle_temp_path.name}, MKV Final: {final_output_mkv_path.name}")
                        
                        if mux_output_temp_path.exists():
                            logging.warning(f"Eliminando existente: {mux_output_temp_path.name}")
                            os.remove(mux_output_temp_path)
                        if should_replace and not mkv_path.exists():
                            raise Exception("Original MKV no encontrado para reemplazo")
                        
                        # --- Comando mkvmerge CORREGIDO ---
                        mkvmerge_cmd_add = [
                            tool_paths['mkvmerge'],
                            '-o', str(mux_output_temp_path), # Salida
                        ]
                        # Insertar --chapters ANTES del MKV de entrada (si se generaron capítulos)
                        if chapter_file_path and chapter_file_path.exists():
                            mkvmerge_cmd_add.extend(['--chapters', str(chapter_file_path)])
                            logging.info("[Chapters] Incluyendo capítulos en mkvmerge: %s", chapter_file_path.name)
                        # Archivo 1 (MKV original)
                        mkvmerge_cmd_add.append(str(mkv_path))
                        # Opciones para Archivo 2 (SRT)
                        mkvmerge_cmd_add.extend([
                            '--language', f"0:{config['PRIMARY_TARGET_CODE']}",
                            '--track-name', f"0:{config['TRANSLATED_TRACK_NAME']}",
                            '--default-track-flag', f"0:{'yes' if config['SET_NEW_SUB_DEFAULT'] else 'no'}",
                        ])
                        # Archivo 2 (SRT)
                        mkvmerge_cmd_add.append(str(saved_subtitle_temp_path))
                        # --- Fin Comando mkvmerge CORREGIDO ---
                        try:
                            logging.info("Ejecutando mkvmerge...")
                            logging.debug("Cmd: %s", ' '.join(shlex.quote(str(p)) for p in mkvmerge_cmd_add))
                            proc_add = subprocess.run(mkvmerge_cmd_add, check=True, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
                            logging.info("Muxing OK -> %s", mux_output_temp_path.name)
                            final_action_successful = True
                        except Exception as e:
                            logging.exception("Error muxing")
                            final_action_successful = False
                        
                        if should_replace and final_action_successful:
                             try:
                                 logging.info(f"Reemplazando '{mkv_path.name}'...")
                                 shutil.move(str(mux_output_temp_path), str(mkv_path))
                                 logging.info("Reemplazo OK!")
                             except Exception as e:
                                 logging.exception(f"ERROR FATAL reemplazo! Muxed en {mux_output_temp_path}")
                                 final_action_successful = False
                        elif should_replace and not final_action_successful:
                             if mux_output_temp_path.exists():
                                try:
                                    os.remove(mux_output_temp_path)
                                    logging.info("Temp mux fallido eliminado.")
                                except Exception as e_remove:
                                    logging.warning(f"No se pudo eliminar temp mux fallido {mux_output_temp_path}: {e_remove}")
                    else:
                        logging.error("mkvmerge no encontrado para 'remux'.")
                elif output_action == 'save_separate_sub':
                    final_action_successful = True
            elif not saved_subtitle_temp_path:
                logging.warning("No se generó sub temp, no hay acción final.")
            # --- FIN Acción Final (CORREGIDO mkvmerge command) ---

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
            action_to_perform = config.get('OUTPUT_ACTION', 'remux')
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
    except SystemExit as e: exit_msg = str(e) if str(e) not in ['None', '0'] else "Salida controlada."; logging.warning(f"Ejecución abortada: {exit_msg}")
    except Exception as e: logging.exception("Error fatal no recuperado:")
    finally:
        if 'translation_cache' in locals() and config.get('ENABLE_TRANSLATION_CACHE'): translation_cache.save_cache()
        else: logging.debug("No se guardó caché.")
    logging.info("--- Proceso Finalizado ---")

if __name__ == "__main__":
    # setup_logging() # Se llama dos veces, main() ya lo hace. Comentado para evitar duplicados.
    main()