"""
chapter_generator.py — Automatic anime chapter generation via OP/ED correlation.

Based on IONI0/SubsPlus-Scripts Auto_Chap.
Downloads anime themes from animethemes.moe, correlates against episode audio,
and generates OGM chapter files for mkvmerge embedding.

All functions return None on failure — ZERO exceptions propagate to the caller.
Heavy imports (soundfile, scipy, numpy) are deferred to function scope.
"""

import logging
import re
import json
import os
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

from src.constants import (
    CHAPTER_NAMES,
    DOWNSAMPLE_FACTOR,
    SILENCE_DURATION,
    SCORE_THRESHOLD,
    THEME_PORTION,
    SNAP_TOLERANCE,
    CORRELATION_TIMEOUT,
    ANIMETHEMES_API_BASE,
    ANIMETHEMES_SEARCH_ENDPOINT,
    ANIMETHEMES_MATCH_THRESHOLD,
    ANIMETHEMES_INCLUDE_PARAM,
    THEME_CACHE_METADATA_FILE,
    THEME_MIN_FILE_SIZE,
)

# Season matching patterns for animethemes.moe results
_SEASON_PATTERNS = [
    re.compile(r'\b(\d+)(?:st|nd|rd|th)\s+[Ss]eason\b'),
    re.compile(r'\b[Ss]eason\s+(\d+)\b'),
    re.compile(r'\bPart\s+(\d+)\b'),
    re.compile(r'\bCour\s+(\d+)\b'),
]


def _normalize_for_match(title: str) -> str:
    """Normalize an anime title for similarity comparison."""
    t = title.strip().lower()
    # Strip subtitle after colon
    if ':' in t:
        t = t.split(':')[0].strip()
    # Remove season indicators using existing patterns
    for pattern in _SEASON_PATTERNS:
        t = pattern.sub('', t)
    # Collapse whitespace
    return ' '.join(t.split())


def _validate_anime_match(query: str, anime_name: str, *, query_is_romaji: bool = True) -> bool:
    """
    Validate that an anime name from API results actually matches the search query.
    Three-stage: exact normalized -> containment -> fuzzy (only if query_is_romaji).
    Returns True if match is valid, False if result should be filtered out.
    """
    if not query_is_romaji:
        # Language mismatch (English query, romaji results) — bypass validation
        return True

    q = _normalize_for_match(query)
    n = _normalize_for_match(anime_name)

    if not q or not n:
        return True  # Can't validate — accept to avoid false rejections

    # Stage 1: Exact normalized match
    if q == n:
        return True

    # Stage 2: Containment check (handles "Naruto" in "Naruto Shippuden")
    if q in n or n in q:
        return True

    # Stage 3: Fuzzy match via SequenceMatcher
    score = SequenceMatcher(None, q, n, autojunk=False).ratio()
    if score >= ANIMETHEMES_MATCH_THRESHOLD:
        logging.debug("[Chapters] Validación fuzzy: '%s' vs '%s' = %.2f (aceptado)", query, anime_name, score)
        return True

    logging.debug("[Chapters] Validación fuzzy: '%s' vs '%s' = %.2f (rechazado, umbral=%.2f)",
                  query, anime_name, score, ANIMETHEMES_MATCH_THRESHOLD)
    return False


def _select_season_entry(anime_list: list, season_number: int, series_title: str) -> dict:
    """Select the anime entry matching the requested season number."""
    if not season_number or season_number <= 1:
        return anime_list[0]

    # Strategy 1: Pattern matching on name and slug (TV only — skip movies/specials)
    for entry in anime_list:
        if entry.get('media_format') == 'Movie':
            continue
        name = entry.get('name', '')
        slug = entry.get('slug', '')
        combined = f"{name} {slug}"
        for pattern in _SEASON_PATTERNS:
            match = pattern.search(combined)
            if match and int(match.group(1)) == season_number:
                logging.info("[Chapters] Temporada %d encontrada por nombre: '%s'", season_number, name)
                return entry

    # Strategy 2: Year-based ordering
    base_words = series_title.strip().lower().split()[0:3]
    base_prefix = ' '.join(base_words)
    related = [e for e in anime_list if e.get('name', '').lower().startswith(base_prefix) and e.get('media_format') != 'Movie']
    if not related:
        related = [e for e in anime_list if e.get('media_format') != 'Movie']
        if not related:
            related = anime_list
    related_sorted = sorted(related, key=lambda e: (e.get('year', 9999), e.get('id', 0)))
    logging.debug("[Chapters] Entradas ordenadas por año (sin películas): %s",
                  [(e.get('name', '?'), e.get('year', '?')) for e in related_sorted])
    if season_number <= len(related_sorted):
        selected = related_sorted[season_number - 1]
        logging.info("[Chapters] Temporada %d seleccionada por orden cronológico: '%s' (año %s)",
                     season_number, selected.get('name', '?'), selected.get('year', '?'))
        return selected

    # Final fallback
    logging.warning("[Chapters] No se encontró entrada para temporada %d de '%s'. "
                    "Usando primera entrada '%s' como fallback.",
                    season_number, series_title, anime_list[0].get('name', '?'))
    return anime_list[0]


# ============================================================
# Section 1: animethemes.moe API Client + Theme Cache
# ============================================================

def search_anime_themes(series_title: str, *, season_number: int | None = None, query_is_romaji: bool = False) -> Optional[Dict[str, Any]]:
    """
    Search animethemes.moe for OP/ED themes matching the series title.
    Returns dict with 'op_themes' and 'ed_themes' keys (lists of dicts).
    Returns None on API failure or no results.
    """
    import requests

    if not series_title or series_title == 'Desconocido':
        logging.warning("[Chapters] Serie sin título válido ('%s'). Omitiendo búsqueda de temas.", series_title)
        return None

    try:
        logging.info("[Chapters] Buscando temas en animethemes.moe para '%s'...", series_title)
        response = requests.get(
            f"{ANIMETHEMES_API_BASE}{ANIMETHEMES_SEARCH_ENDPOINT}",
            params={
                'q': series_title,
                'fields[search]': 'anime',
                'include[anime]': ANIMETHEMES_INCLUDE_PARAM,
            },
            timeout=30,
        )

        # Handle rate limiting
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', '30'))
            retry_after = min(retry_after, 60)  # Cap at 60s
            logging.warning("[Chapters] Rate limited (429). Esperando %ds...", retry_after)
            time.sleep(retry_after)
            response = requests.get(
                f"{ANIMETHEMES_API_BASE}{ANIMETHEMES_SEARCH_ENDPOINT}",
                params={
                    'q': series_title,
                    'fields[search]': 'anime',
                    'include[anime]': ANIMETHEMES_INCLUDE_PARAM,
                },
                timeout=30,
            )
            if response.status_code == 429:
                logging.warning("[Chapters] Rate limited de nuevo. Omitiendo capítulos.")
                return None

        if response.status_code == 404:
            logging.info("[Chapters] Serie no encontrada en animethemes.moe.")
            return None

        response.raise_for_status()
        data = response.json()

        # Navigate response: {"search": {"anime": [...]}} -> animethemes[] -> entries[] -> videos[] -> audio
        search_data = data.get('search')
        if search_data is None:
            logging.warning("[Chapters] Respuesta API inesperada (sin clave 'search'). Raw keys: %s", list(data.keys()))
            return None
        anime_list = search_data.get('anime', [])
        if not anime_list:
            logging.info("[Chapters] Sin resultados para '%s'.", series_title)
            return None

        # Validate results against search query (only when query is romaji)
        if query_is_romaji:
            validated = [a for a in anime_list
                         if _validate_anime_match(series_title, a.get('name', ''), query_is_romaji=True)]
            if not validated:
                logging.warning("[Chapters] Ningún resultado de animethemes.moe coincide con '%s' "
                                "(mejores: %s). Omitiendo capítulos.",
                                series_title,
                                [a.get('name', '?') for a in anime_list[:3]])
                return None
            if len(validated) < len(anime_list):
                logging.info("[Chapters] Filtrados %d/%d resultados por validación de nombre.",
                             len(anime_list) - len(validated), len(anime_list))
            anime_list = validated

        # Select the correct season entry from results
        anime = _select_season_entry(anime_list, season_number, series_title)
        animethemes = anime.get('animethemes', [])
        if not animethemes:
            logging.info("[Chapters] Anime encontrado pero sin temas registrados.")
            return None

        result = {'op_themes': [], 'ed_themes': []}

        for theme in animethemes:
            theme_type = theme.get('type', '').upper()
            slug = theme.get('slug', '')

            if theme_type not in ('OP', 'ED'):
                continue

            entries = theme.get('animethemeentries', [])
            if not entries:
                continue

            # Get audio URL from first entry -> first video -> audio
            audio_url = None
            for entry in entries:
                videos = entry.get('videos', [])
                for video in videos:
                    audio = video.get('audio')
                    if audio and audio.get('link'):
                        audio_url = audio['link']
                        break
                if audio_url:
                    break

            if not audio_url:
                continue

            key = 'op_themes' if theme_type == 'OP' else 'ed_themes'
            result[key].append({'url': audio_url, 'slug': slug})
            logging.info("[Chapters] Tema encontrado: %s (url=%s...)", slug, audio_url[:60])

        if not result['op_themes'] and not result['ed_themes']:
            logging.info("[Chapters] No se encontraron URLs de audio para temas.")
            return None

        logging.info("[Chapters] Total temas: %d OP, %d ED",
                     len(result['op_themes']), len(result['ed_themes']))
        return result

    except Exception as e:
        logging.warning("[Chapters] Error buscando temas: %s", e)
        return None


def _download_theme_file(url: str, dest_path: Path) -> bool:
    """
    Download a theme audio file with atomic write pattern.
    Returns True on success, False on failure.
    """
    import requests

    tmp_path = dest_path.with_suffix(dest_path.suffix + '.tmp')
    try:
        logging.debug("[Chapters] Descargando tema: %s -> %s", url, dest_path.name)
        response = requests.get(url, timeout=120, stream=True)
        response.raise_for_status()

        with open(tmp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Validate file size
        if tmp_path.stat().st_size < THEME_MIN_FILE_SIZE:
            logging.warning("[Chapters] Tema descargado demasiado pequeño (%d bytes). Descartando.", tmp_path.stat().st_size)
            tmp_path.unlink(missing_ok=True)
            return False

        # Atomic rename
        os.replace(str(tmp_path), str(dest_path))
        logging.info("[Chapters] Tema descargado OK: %s (%d bytes)", dest_path.name, dest_path.stat().st_size)
        return True

    except Exception as e:
        logging.warning("[Chapters] Error descargando tema: %s", e)
        tmp_path.unlink(missing_ok=True)
        return False


def get_theme_files(
    series_title: str,
    theme_info: Dict[str, Any],
    cache_dir: Optional[Path],
) -> Dict[str, List[Path]]:
    """
    Get ALL theme audio files for the season, using cache if available.
    Returns dict with 'op' and 'ed' keys pointing to lists of file Paths.
    """
    result = {'op': [], 'ed': []}

    if not cache_dir:
        logging.warning("[Chapters] Sin directorio de caché de temas.")
        return result

    os.makedirs(cache_dir, exist_ok=True)
    series_dir = cache_dir / _sanitize_dirname(series_title)
    os.makedirs(series_dir, exist_ok=True)

    # Check/load metadata cache
    metadata_path = series_dir / THEME_CACHE_METADATA_FILE
    metadata = {}
    if metadata_path.exists():
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        except Exception:
            metadata = {}

    themes_cache = metadata.get('themes', {})
    changed = False

    for theme_type, key in [('op', 'op_themes'), ('ed', 'ed_themes')]:
        themes_list = theme_info.get(key, [])
        for theme in themes_list:
            url = theme.get('url')
            slug = theme.get('slug', theme_type)
            if not url:
                continue

            filename = f"{slug}.ogg"
            file_path = series_dir / filename

            # Check cache: file exists, size OK, URL matches
            cached = themes_cache.get(slug, {})
            if (file_path.exists()
                    and file_path.stat().st_size >= THEME_MIN_FILE_SIZE
                    and cached.get('url') == url):
                logging.info("[Chapters] Usando tema cacheado: %s", file_path.name)
                result[theme_type].append(file_path)
                continue

            # Download
            if _download_theme_file(url, file_path):
                result[theme_type].append(file_path)
                themes_cache[slug] = {'url': url, 'file': filename}
                changed = True

    # Save metadata (new format)
    if changed:
        metadata['themes'] = themes_cache
        tmp_metadata = metadata_path.with_suffix('.tmp')
        try:
            with open(tmp_metadata, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
            os.replace(str(tmp_metadata), str(metadata_path))
        except Exception as e:
            logging.warning("[Chapters] No se pudo guardar metadata de caché: %s", e)
            if tmp_metadata.exists():
                tmp_metadata.unlink(missing_ok=True)

    return result


def _sanitize_dirname(name: str) -> str:
    """Sanitize a string for use as a directory name."""
    # Remove characters not allowed in directory names
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    sanitized = sanitized.strip('. ')
    return sanitized or 'unknown'


# ============================================================
# Section 2: Audio Extraction (ffmpeg) + Loading (soundfile)
# ============================================================

def extract_episode_audio(mkv_path: Path, tmpdir: Path) -> Optional[Path]:
    """
    Extract first audio track from MKV to WAV using ffmpeg.
    Returns path to extracted WAV, or None on failure.
    """
    import subprocess

    output_wav = tmpdir / "episode_audio.wav"
    cmd = [
        'ffmpeg',
        '-i', str(mkv_path),
        '-map', '0:a:0',       # First audio track
        '-ac', '1',            # Mono
        '-ar', '22050',        # Target sample rate for correlation
        '-y',                  # Overwrite
        str(output_wav),
    ]

    try:
        logging.info("[Chapters] Extrayendo audio del episodio con ffmpeg...")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=300,
        )
        if proc.returncode != 0:
            logging.warning("[Chapters] ffmpeg falló (código %d): %s", proc.returncode, proc.stderr[-500:] if proc.stderr else 'sin salida')
            return None

        if not output_wav.exists() or output_wav.stat().st_size == 0:
            logging.warning("[Chapters] Audio extraído vacío o no creado.")
            return None

        logging.info("[Chapters] Audio extraído OK: %s (%.1f MB)", output_wav.name, output_wav.stat().st_size / 1048576)
        return output_wav

    except subprocess.TimeoutExpired:
        logging.warning("[Chapters] Timeout extrayendo audio (>300s).")
        return None
    except FileNotFoundError:
        logging.warning("[Chapters] ffmpeg no encontrado en PATH. Instalar vía docker-mods.")
        return None
    except Exception as e:
        logging.warning("[Chapters] Error extrayendo audio: %s", e)
        return None


def load_and_downsample(audio_path: Path, downsample_factor: int) -> Optional[Tuple]:
    """
    Load audio with soundfile and downsample for correlation.
    Returns (downsampled_array, sample_rate) or None on failure.

    CRITICAL: Always resamples to sr=22050 for consistency between episode and theme audio.
    Uses soundfile instead of librosa to avoid heavy dependencies (scikit-learn, numba).
    Falls back to ffmpeg conversion for OGG Opus files that libsndfile can't decode.
    """
    try:
        import soundfile as sf
        import numpy as np

        target_sr = 22050
        logging.debug("[Chapters] Cargando audio: %s", audio_path.name)

        # Try soundfile first (works for WAV and most formats)
        try:
            data, sr_native = sf.read(str(audio_path), dtype='float32')
        except Exception as sf_err:
            # Fallback: convert to WAV via ffmpeg (handles OGG Opus that libsndfile can't decode)
            if audio_path.suffix.lower() in ('.ogg', '.opus', '.webm'):
                logging.debug("[Chapters] soundfile falló para '%s' (%s). Intentando conversión ffmpeg...",
                              audio_path.name, sf_err)
                wav_path = audio_path.with_suffix('.wav')
                try:
                    import subprocess
                    proc = subprocess.run(
                        ['ffmpeg', '-i', str(audio_path), '-ac', '1', '-ar', str(target_sr),
                         '-y', str(wav_path)],
                        capture_output=True, text=True, encoding='utf-8', errors='replace',
                        timeout=60,
                    )
                    if proc.returncode != 0 or not wav_path.exists():
                        logging.warning("[Chapters] ffmpeg conversión falló para '%s': %s",
                                        audio_path.name, proc.stderr[-300:] if proc.stderr else '')
                        return None
                    data, sr_native = sf.read(str(wav_path), dtype='float32')
                    logging.debug("[Chapters] Audio convertido via ffmpeg: %s -> %s", audio_path.name, wav_path.name)
                except Exception as ff_err:
                    logging.warning("[Chapters] Fallback ffmpeg falló para '%s': %s", audio_path.name, ff_err)
                    return None
            else:
                raise sf_err  # Re-raise for non-OGG files

        # Convert stereo to mono if needed (soundfile returns (samples, channels) for stereo)
        if data.ndim > 1:
            data = np.mean(data, axis=1)

        if len(data) == 0:
            logging.warning("[Chapters] Audio cargado vacío: %s", audio_path.name)
            return None

        # Resample to target_sr if source has different sample rate
        if sr_native != target_sr:
            from scipy.signal import resample
            num_samples = int(len(data) * target_sr / sr_native)
            data = resample(data, num_samples).astype(np.float32)
            logging.debug("[Chapters] Resampleado: %d Hz -> %d Hz", sr_native, target_sr)

        sr = target_sr

        # Downsample by integer slicing (fast approximation for correlation)
        y_ds = data[::downsample_factor]
        effective_sr = sr / downsample_factor

        logging.debug(
            "[Chapters] Audio cargado: %s | original=%d samples (%.1fs) | downsampled=%d samples | effective_sr=%.0f Hz",
            audio_path.name, len(data), len(data) / sr, len(y_ds), effective_sr,
        )
        return (y_ds, effective_sr)

    except Exception as e:
        logging.warning("[Chapters] Error cargando audio '%s': %s", audio_path.name, e)
        return None


# ============================================================
# Section 3: Correlation Engine
# ============================================================

def _correlate_worker(episode_data, theme_data, score_threshold: int) -> Tuple[Optional[float], float]:
    """
    Perform cross-correlation between episode and theme audio.
    Returns (offset_seconds, peak_score) if a match is found above threshold, else (None, 0.0).

    MUST be a module-level function for Windows multiprocessing pickle compatibility.
    """
    import numpy as np
    from scipy.signal import correlate

    episode_ds, episode_sr = episode_data
    theme_ds, _ = theme_data

    # Cross-correlate
    c = correlate(episode_ds, theme_ds, mode='valid')

    # Find peak (use max, not abs — matching IONI0 original)
    peak_idx = np.argmax(c)
    peak_score = float(c[peak_idx])

    if peak_score < score_threshold:
        return (None, 0.0)

    # Convert sample index to seconds
    offset_seconds = peak_idx / episode_sr
    return (offset_seconds, peak_score)


def correlate_theme(
    episode_data: Tuple,
    theme_data: Tuple,
    config: dict,
    *,
    return_score: bool = False,
) -> Any:
    """
    Run correlation with timeout protection.
    Returns offset in seconds (or (offset, score) if return_score=True), or None on failure/timeout.

    Uses ProcessPoolExecutor for Windows-safe timeout (signal.alarm is UNIX-only).
    """
    from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError

    timeout = config.get('CORRELATION_TIMEOUT', CORRELATION_TIMEOUT)
    score_threshold = config.get('SCORE_THRESHOLD', SCORE_THRESHOLD)
    downsample_factor = config.get('DOWNSAMPLE_FACTOR', DOWNSAMPLE_FACTOR)
    # Match IONI0/SubsPlus-Scripts: divide threshold by downsample factor
    adjusted_threshold = score_threshold / downsample_factor

    try:
        logging.debug("[Chapters] Iniciando correlación (timeout=%ds, threshold=%.1f, raw=%d, downsample=%d)...",
                      timeout, adjusted_threshold, score_threshold, downsample_factor)
        with ProcessPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _correlate_worker,
                episode_data,
                theme_data,
                adjusted_threshold,
            )
            result = future.result(timeout=timeout)

        # result is (offset, score) or (None, 0.0) from _correlate_worker
        offset, score = result if result else (None, 0.0)

        if offset is not None:
            logging.info("[Chapters] Correlación encontrada: offset=%.2fs (score=%.1f)", offset, score)
        else:
            logging.info("[Chapters] Correlación por debajo del umbral.")

        return (offset, score) if return_score else offset

    except FuturesTimeoutError:
        logging.warning("[Chapters] Timeout en correlación (>%ds). Omitiendo.", timeout)
        return (None, 0.0) if return_score else None
    except Exception as e:
        logging.warning("[Chapters] Error en correlación: %s", e)
        return (None, 0.0) if return_score else None


def find_chapter_offsets(
    episode_data: Tuple,
    theme_files: Dict[str, List[Path]],
    config: dict,
) -> dict[str, float | None]:
    """
    Find OP and ED offsets in the episode audio.
    Returns dict with 'op_start', 'op_end', 'ed_start', 'ed_end' keys (values in seconds or None).
    """
    offsets = {'op_start': None, 'op_end': None, 'ed_start': None, 'ed_end': None}
    downsample_factor = config.get('DOWNSAMPLE_FACTOR', DOWNSAMPLE_FACTOR)
    silence_duration = config.get('SILENCE_DURATION', SILENCE_DURATION)
    snap_tolerance = config.get('SNAP_TOLERANCE', SNAP_TOLERANCE)

    episode_ds, episode_sr = episode_data

    # Calculate episode duration from downsampled data
    episode_duration = len(episode_ds) / episode_sr

    for theme_type in ['op', 'ed']:
        theme_paths = theme_files.get(theme_type, [])
        if not theme_paths:
            continue

        best_offset = None
        best_score = -1
        best_slug = None
        best_theme_duration = None

        for theme_path in theme_paths:
            theme_data = load_and_downsample(theme_path, downsample_factor)
            if not theme_data:
                continue

            theme_ds, _ = theme_data
            theme_duration = len(theme_ds) / episode_sr

            # Use only a portion of the theme for correlation (handles cut-off/fade-out)
            theme_portion = config.get('THEME_PORTION', THEME_PORTION)
            theme_ds_sliced = theme_ds[:int(len(theme_ds) * theme_portion)]

            # Prepend silence to episode for detecting themes at the very start
            import numpy as np
            silence_samples = int(silence_duration * episode_sr)
            episode_with_silence = np.concatenate([
                np.zeros(silence_samples, dtype=episode_ds.dtype),
                episode_ds,
            ])

            offset, score = correlate_theme(
                (episode_with_silence, episode_sr),
                (theme_ds_sliced, episode_sr),
                config,
                return_score=True,
            )

            slug = theme_path.stem  # e.g., "OP2" from "OP2.ogg"
            if offset is not None and score > best_score:
                best_offset = offset
                best_score = score
                best_slug = slug
                best_theme_duration = theme_duration
                logging.debug("[Chapters] %s candidato: %s offset=%.2fs score=%.1f",
                              theme_type.upper(), slug, offset, score)

        if best_offset is not None:
            # Adjust for prepended silence
            best_offset -= silence_duration

            # Snap to episode boundaries
            if abs(best_offset) <= snap_tolerance:
                best_offset = 0.0
            if abs(best_offset + best_theme_duration - episode_duration) <= snap_tolerance:
                best_offset = episode_duration - best_theme_duration

            # Clamp to valid range
            best_offset = max(0.0, best_offset)

            offsets[f'{theme_type}_start'] = best_offset
            offsets[f'{theme_type}_end'] = best_offset + best_theme_duration
            logging.info(
                "[Chapters] %s seleccionado: %s (%.2fs - %.2fs, score=%.1f)",
                theme_type.upper(), best_slug, best_offset,
                best_offset + best_theme_duration, best_score,
            )

    return offsets


# ============================================================
# Section 4: OGM Chapter Writer
# ============================================================

def _format_timestamp(seconds: float) -> str:
    """Format seconds to OGM timestamp: HH:MM:SS.mmm"""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def write_ogm_chapters(
    offsets: Dict[str, Optional[float]],
    episode_duration: float,
    output_path: Path,
) -> Optional[Path]:
    """
    Write OGM chapter file based on detected theme offsets.

    Chapter structure (all that apply):
    - Prologue: 00:00:00.000 (if OP doesn't start at 0)
    - Opening: OP start time
    - Episode: After OP ends (or 00:00:00.000 if no OP)
    - Ending: ED start time
    - Epilogue: After ED ends (if ED doesn't end at episode end)

    Returns path to written chapter file, or None on failure.
    Only writes chapters with valid, non-None timestamps.
    """
    try:
        chapters = []

        op_start = offsets.get('op_start')
        op_end = offsets.get('op_end')
        ed_start = offsets.get('ed_start')
        ed_end = offsets.get('ed_end')

        # Build chapter list based on what we found
        if op_start is not None and op_start > 1.0:
            # There's content before the OP — that's a Prologue
            chapters.append((0.0, CHAPTER_NAMES['prologue']))

        if op_start is not None:
            chapters.append((op_start, CHAPTER_NAMES['opening']))

        # Episode part starts after OP ends, or at 0 if no OP
        if op_end is not None:
            chapters.append((op_end, CHAPTER_NAMES['episode']))
        elif op_start is None:
            # No OP found at all — episode is the whole thing (unless ED marks it)
            chapters.append((0.0, CHAPTER_NAMES['episode']))

        if ed_start is not None:
            chapters.append((ed_start, CHAPTER_NAMES['ending']))

        if ed_end is not None and ed_end < (episode_duration - 1.0):
            # There's content after the ED — that's an Epilogue
            chapters.append((ed_end, CHAPTER_NAMES['epilogue']))

        if not chapters:
            logging.info("[Chapters] No se generaron capítulos (sin offsets válidos).")
            return None

        # Sort by timestamp
        chapters.sort(key=lambda x: x[0])

        # Remove duplicates (same timestamp)
        deduped = [chapters[0]]
        for ch in chapters[1:]:
            if abs(ch[0] - deduped[-1][0]) > 0.5:  # More than 0.5s apart
                deduped.append(ch)
        chapters = deduped

        # Write OGM format (UTF-8, no BOM)
        with open(output_path, 'w', encoding='utf-8') as f:
            for i, (timestamp, name) in enumerate(chapters, start=1):
                f.write(f"CHAPTER{i:02d}={_format_timestamp(timestamp)}\n")
                f.write(f"CHAPTER{i:02d}NAME={name}\n")

        logging.info("[Chapters] Archivo OGM escrito: %s (%d capítulos)", output_path.name, len(chapters))
        for ts, name in chapters:
            logging.info("[Chapters]   %s = %s", _format_timestamp(ts), name)

        return output_path

    except Exception as e:
        logging.warning("[Chapters] Error escribiendo capítulos OGM: %s", e)
        return None


# ============================================================
# Section 5: Orchestrator
# ============================================================

def generate_chapters(series_title: str, mkv_path: Path, tmpdir: Path, config: dict, *, season_number: int | None = None) -> Optional[Path]:
    """
    Orquestador principal para la generación de capítulos.
    Coordina la búsqueda de temas, extracción de audio, correlación y escritura de capítulos.
    """
    try:
        logging.info("[Chapters] Iniciando generación de capítulos para '%s'...", series_title)
        # 0.5. Buscar título romanizado (x-jat) para mejor match en animethemes.moe
        cache_dir_str = config.get('CHAPTERS_THEME_CACHE_DIR')
        _query_is_romaji = False
        if cache_dir_str:
            try:
                from src.title_lookup import lookup_romaji_title
                romaji = lookup_romaji_title(series_title, Path(cache_dir_str))
                if romaji:
                    logging.info("[Chapters] Usando título romaji: '%s' (original: '%s')", romaji, series_title)
                    series_title = romaji
                    _query_is_romaji = True
                else:
                    logging.info("[Chapters] Sin título romaji. Usando original: '%s'", series_title)
            except Exception as e:
                logging.warning("[Chapters] Error en title_lookup (ignorado): %s", e)


        # 1. Buscar información de temas en animethemes.moe
        theme_info = search_anime_themes(series_title, season_number=season_number, query_is_romaji=_query_is_romaji)
        if not theme_info:
            return None

        # 2. Determinar directorio de caché para los temas
        cache_dir_str = config.get('CHAPTERS_THEME_CACHE_DIR')
        cache_dir = Path(cache_dir_str) if cache_dir_str else tmpdir

        # 3. Descargar u obtener archivos de audio de los temas
        theme_files = get_theme_files(series_title, theme_info, cache_dir)
        if not theme_files.get('op') and not theme_files.get('ed'):
            return None

        # 4. Extraer audio del archivo MKV del episodio
        episode_wav = extract_episode_audio(mkv_path, tmpdir)
        if not episode_wav:
            return None

        # 5. Cargar y remuestrear el audio del episodio
        downsample_factor = config.get('DOWNSAMPLE_FACTOR', DOWNSAMPLE_FACTOR)
        episode_data = load_and_downsample(episode_wav, downsample_factor)
        if not episode_data:
            return None

        # 6. Encontrar los offsets de tiempo para los capítulos mediante correlación
        offsets = find_chapter_offsets(episode_data, theme_files, config)
        if all(v is None for v in offsets.values()):
            logging.info("[Chapters] No se encontraron coincidencias de temas para '%s'", series_title)
            return None

        # 7. Calcular duración del episodio y escribir archivo de capítulos OGM
        # episode_data es una tupla (y, sr)
        episode_duration = len(episode_data[0]) / episode_data[1]
        chapter_path = tmpdir / "chapters.ogm"

        return write_ogm_chapters(offsets, episode_duration, chapter_path)

    except Exception as e:
        logging.warning("[Chapters] Error inesperado generando capítulos: %s", e)
        return None
