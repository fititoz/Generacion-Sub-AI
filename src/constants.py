"""
constants.py — Shared constants, regex patterns, and configuration defaults.

Central registry for all magic values used across modules: tag regex,
chapter names, correlation engine parameters, API endpoints, and cache settings.
"""
import re

# --- Regex y Constantes ---
TAG_REGEX = re.compile(
    r'('
    r'<[^>]+>'  # Etiquetas HTML: <...>
    r'|'
    # Etiquetas ASS/SSA: {...} que contienen al menos un '\' O comandos comunes sin él
    r'\{\s*\\' # Llave + opcional espacio + barra invertida (captura \N, \pos, \c, \fs, \alpha, etc.)
    r'[^}]*?'   # Cualquier cosa hasta la llave de cierre (no codicioso)
    r'\}'
    r'|'
    # Comandos comunes sin barra inicial (menos frecuente, pero posible)
    # \b asegura que sea una palabra completa (evita coincidencias parciales)
    # Añadidos más comandos comunes de ASS
    r'\{\s*(?:an|pos|fad|fade|move|org|bord|shad|be|blur|fs|fn|c1|c2|c3|c4|alpha|i|b|u|s|kf|ko|k|K|p|1c|2c|3c|4c|1a|2a|3a|4a|fscx|fscy|frx|fry|frz|clip|t|pbo|fsplus|fsminus)\b'
    r'[^}]*?' # Resto hasta la llave
    r'\}'
    r')'
)

# Regex para saltos de línea ASS/SSA sueltos (fuera de llaves)
LINEBREAK_REGEX = re.compile(r'\\[Nnh]')

CACHE_DIR_NAME = "cache"
CACHE_FILE_NAME = "gemini_translation_cache.json"
LOG_FILENAME = 'translate_mkv_subs.log'
PLACEHOLDER_PREFIX = "__TAG"
PLACEHOLDER_SUFFIX = "__"
BATCH_FAILURE_INDICATOR = "[[BATCH_TRANSLATION_FAILED_IRRECOVERABLY]]"

REQUIRED_PACKAGES = {
    "google-genai": "google-genai",
    "pysubs2": "pysubs2",
    "pymkv": "pymkv",
}

# Dependencias opcionales para generación de capítulos (import_name: pip_name)
CHAPTER_PACKAGES = {
    "scipy": "scipy",
    "numpy": "numpy",
    "requests": "requests",
    "soundfile": "soundfile",
}

# --- Chapter Generation Constants ---
CHAPTER_NAMES = {
    'prologue': 'Prologue',
    'opening': 'Opening',
    'episode': 'Episode',
    'ending': 'Ending',
    'epilogue': 'Epilogue',
}

# Correlation engine defaults
DOWNSAMPLE_FACTOR = 32
SILENCE_DURATION = 5.0  # seconds of silence prepended to episode audio
SCORE_THRESHOLD = 2000  # minimum correlation peak score
THEME_PORTION = 0.9     # fraction of theme to use for correlation (handles cut-off/fade-out)
SNAP_TOLERANCE = 4.0    # seconds — snap to start/end if within this tolerance
CORRELATION_TIMEOUT = 120  # seconds — hard timeout for correlation computation

# animethemes.moe API
ANIMETHEMES_API_BASE = 'https://api.animethemes.moe'
ANIMETHEMES_SEARCH_ENDPOINT = '/search'
ANIMETHEMES_INCLUDE_PARAM = 'animethemes.animethemeentries.videos.audio'

# Theme cache
THEME_CACHE_METADATA_FILE = 'data.json'
THEME_MIN_FILE_SIZE = 51200  # 50KB — reject partial downloads

# Title lookup parsed cache
ANIMETITLES_PARSED_CACHE_FILENAME = 'animetitles_parsed.json'
TITLE_CACHE_TTL_SECONDS = 86400  # 24 hours

# animethemes.moe result validation
ANIMETHEMES_MATCH_THRESHOLD = 0.75  # minimum SequenceMatcher ratio for anime name validation
