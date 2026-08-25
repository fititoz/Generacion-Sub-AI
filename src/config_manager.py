"""
config_manager.py — Configuración efectiva (módulo dueño de las claves).

Una sola tabla (SPEC) define cada clave: sección INI, nombre físico,
coerción y default. La interfaz pública es `load_config(path)` que devuelve
una `Config` inmutable con atributos tipados planos — las secciones del INI
son detalle de almacenaje, no interfaz — más la lista de avisos
(claves desconocidas, valores inválidos).

`FileContext` es el contexto por-archivo (títulos, temporada, capítulos
habilitados) que antes se inyectaba mutando el dict de configuración.

Paridad garantizada por tests/test_config_parity.py:
example <-> SPEC <-> consumidores (atributos tipados, sin claves sueltas).
"""
import configparser
import logging
from dataclasses import dataclass
from pathlib import Path

# --- Defaults de plantillas ([PROMPTS]) ---

DEFAULT_BATCH_PROMPT = '''Contexto: Estás traduciendo el anime "{series_title}" - Episodio: "{episode_title}".
    Eres un traductor experto de subtítulos especializado en anime. Traduce las siguientes {batch_size_info} líneas de subtítulos al {target_language_name}, siguiendo estas reglas ESTRICTAMENTE:
    1.  **Idioma y Tono**: Usa {target_language_name} natural y sin censura. No traduzcas si el texto ya está en español o si es irrelevante. Tuteo por defecto; voseo únicamente si el nombre del idioma lo indica explícitamente. Traduce SIEMPRE todo el texto: nunca devuelvas el original sin traducir.
    2.  **Preservación**: Mantén SIEMPRE intactos: Nombres propios, términos específicos (Oshiruko, Kunai, etc.), honoríficos japoneses (-san, -chan, -sama).
    3.  **Formato de Entrada/Salida**: Cada línea viene numerada como [N]: Texto. La numeración empieza en [1] —nunca en [0]— y el número va entre corchetes, sin negritas ni símbolos adicionales.
    4.  **DEBES responder manteniendo EXACTAMENTE el mismo formato: [N]: Traducción.**
    5.  **Placeholders**: Preserva etiquetas como __TAG0__, __TAG1__ EXACTAMENTE en su posición relativa.
    6.  **Ortografía y Puntuación**: Usa ortografía impecable. Incluye signos de apertura (¿, ¡), tildes correctas y la letra ñ. Evita modismos regionales excesivos a menos que sea necesario para el tono.
    7.  **Consistencia**: Responde ÚNICAMENTE con las líneas traducidas numeradas. Sin preámbulos, sin frases de cierre ("Aquí tienes:", "Espero que ayude") y sin markdown alrededor del formato.
    8.  **Horarios y números**: los números con dos puntos dentro del texto (p. ej. 21:45) se escriben tal cual; el prefijo [N]: aparece solo al inicio de cada línea.
    9.  **Extensión**: mantén una longitud natural similar a la línea original cuando sea posible.

    Líneas a traducir:
    {batch_text}

    Traducciones:'''

DEFAULT_SINGLE_PROMPT = '''Contexto: Episodio "{episode_title}" de la serie "{series_title}".

    Eres un traductor experto de subtítulos especializado en anime. Traduce la siguiente línea al {target_language_name} siguiendo estas reglas:

    1.  **Idioma y Tono**: {target_language_name} natural y sin censura. Tuteo por defecto; voseo únicamente si el nombre del idioma lo indica explícitamente. Traduce SIEMPRE: nunca devuelvas el texto original sin traducir.
    2.  **Preservación**: Mantén intactos los nombres propios, términos específicos y honoríficos japoneses (-san, -chan, -sama).
    3.  **Placeholders**: Si el texto contiene etiquetas como __TAG0__, consérvalas EXACTAMENTE donde están.
    4.  **Formato**: Devuelve ÚNICAMENTE la traducción —sin comillas que no estén en el original—, sin markdown, sin preámbulos ni explicaciones.
    5.  **Extensión**: mantén una longitud natural similar a la línea original cuando sea posible.

    Texto original:
    {text}

    Traducción:'''


# --- Coerciones ---

def _as_str(v):
    """Coerción identidad (str)."""
    return v


def _as_int(v):
    """Coerción a int (ValueError si no es numérico)."""
    return int(v)


def _as_float(v):
    """Coerción a float (ValueError si no es numérico)."""
    return float(v)

_TRUE = {'yes', 'true', 'on', '1'}
_FALSE = {'no', 'false', 'off', '0'}

def _as_bool(v):
    """yes/no/true/false/on/off/1/0 -> bool; otro valor -> ValueError."""
    s = str(v).strip().lower()
    if s in _TRUE: return True
    if s in _FALSE: return False
    raise ValueError(f"valor booleano no reconocido: {v!r}")

def _as_action(v):
    """Normaliza y valida la acción de salida contra la lista permitida."""
    s = v.strip().lower()
    if s not in ('remux', 'save_separate_sub'):
        logging.warning("output_action inválido '%s'. Usando 'save_separate_sub'.", s)
        s = 'save_separate_sub'
    return s

def _as_path_opt(v):
    """str recortado -> Path|None (vacío = None)."""
    s = v.strip()
    return Path(s) if s else None

def _as_csv_tuple(v):
    """CSV -> tupla de códigos en minúsculas (default ['spa'] si vacío)."""
    items = [c.strip().lower() for c in v.split(',') if c.strip()]
    return tuple(items) or ('spa',)

def _as_csv_set(v):
    """CSV -> frozenset en minúsculas (keywords de variantes)."""
    return frozenset(c.strip().lower() for c in v.split(',') if c.strip())

def _as_prompt(v):
    """Plantilla de prompt: solo recorte exterior; formato interno intacto."""
    return v.strip()


# --- Tabla única: attr -> (sección, clave_ini, coerción, default_str) ---
# Toda clave nueva DEBE nacer aquí, en config.ini.example y en su consumidor
# como atributo tipado. El test de paridad cruza los tres mundos.

SPEC = {
    # [API]
    'api_key':                  ('API', 'api_key', _as_str, 'TU_API_KEY_AQUI'),
    'base_url':                 ('API', 'base_url', _as_str, 'http://localhost:20128/v1'),
    # [LLM_SETTINGS]
    'model':                    ('LLM_SETTINGS', 'model', _as_str, 'auto/best-free'),
    'batch_size':               ('LLM_SETTINGS', 'batch_size', _as_int, '50'),
    'api_call_delay':           ('LLM_SETTINGS', 'api_call_delay', _as_float, '5.0'),
    'api_single_timeout':       ('LLM_SETTINGS', 'api_single_timeout', _as_int, '120'),
    'api_batch_timeout':        ('LLM_SETTINGS', 'api_batch_timeout', _as_int, '300'),
    'rate_limit_wait_seconds':  ('LLM_SETTINGS', 'rate_limit_wait_seconds', _as_int, '60'),
    'temperature':              ('LLM_SETTINGS', 'temperature', _as_float, '0.3'),
    'max_tokens':               ('LLM_SETTINGS', 'max_tokens', _as_int, '2000'),
    'enable_translation_cache': ('LLM_SETTINGS', 'enable_translation_cache', _as_bool, 'yes'),
    # [MKV_OUTPUT]
    'mkvtoolnix_dir':           ('MKV_OUTPUT', 'mkvtoolnix_dir', _as_path_opt, ''),
    'set_new_sub_default':      ('MKV_OUTPUT', 'set_new_sub_default', _as_bool, 'yes'),
    'translated_track_name':    ('MKV_OUTPUT', 'translated_track_name', _as_str, 'Español Latino (AI)'),
    'output_mkv_suffix':        ('MKV_OUTPUT', 'output_mkv_suffix', _as_str, '.traducido'),
    'replace_original_mkv':     ('MKV_OUTPUT', 'replace_original_mkv', _as_bool, 'no'),
    'output_action':            ('MKV_OUTPUT', 'output_action', _as_action, 'save_separate_sub'),
    'reorder_existing_tracks':  ('MKV_OUTPUT', 'reorder_existing_tracks', _as_bool, 'yes'),
    'target_language_name':     ('MKV_OUTPUT', 'target_language_name', _as_str, 'Español Latino (sin censura)'),
    'target_language_codes':    ('MKV_OUTPUT', 'target_language_codes', _as_csv_tuple, 'es-419, lat, es, spa, und'),
    'preferred_source_lang':    ('MKV_OUTPUT', 'preferred_source_lang', _as_str, 'fre'),
    'latino_keywords':          ('MKV_OUTPUT', 'latino_keywords', _as_csv_set, 'latino, latin, latam, latin america, español americano, animo, Spanish[LAT]'),
    'spain_keywords':           ('MKV_OUTPUT', 'spain_keywords', _as_csv_set, 'españa, spain, castellano, castilian, español europeo, iberian, european, Spanish[ESP]'),
    'required_sonarr_tag':      ('MKV_OUTPUT', 'required_sonarr_tag', _as_str, ''),
    # [PROMPTS]
    'prompt_batch_template':    ('PROMPTS', 'batch_template', _as_prompt, DEFAULT_BATCH_PROMPT),
    'prompt_single_template':   ('PROMPTS', 'single_template', _as_prompt, DEFAULT_SINGLE_PROMPT),
    # [CHAPTERS]
    'chapters_enabled':         ('CHAPTERS', 'enabled', _as_bool, 'yes'),
    'theme_cache_dir':          ('CHAPTERS', 'theme_cache_dir', _as_path_opt, '/config/scripts/OPED'),
    'max_theme_cache_mb':       ('CHAPTERS', 'max_theme_cache_mb', _as_int, '1024'),
    'theme_cache_ttl_days':     ('CHAPTERS', 'theme_cache_ttl_days', _as_int, '120'),
    'correlation_timeout':      ('CHAPTERS', 'correlation_timeout', _as_int, '120'),
    'score_threshold':          ('CHAPTERS', 'score_threshold', _as_int, '2000'),
    'snap_tolerance':           ('CHAPTERS', 'snap_tolerance', _as_float, '4.0'),
    'silence_duration':         ('CHAPTERS', 'silence_duration', _as_float, '5.0'),
    'downsample_factor':        ('CHAPTERS', 'downsample_factor', _as_int, '32'),
    'theme_portion':            ('CHAPTERS', 'theme_portion', _as_float, '0.9'),
    'anime_path':               ('CHAPTERS', 'anime_path', _as_path_opt, '/home/biblioteca/Anime'),
    # [DEBUG]
    'debug_translation':        ('DEBUG', 'debug_translation', _as_bool, 'false'),
    'debug_max_chars':          ('DEBUG', 'debug_max_chars', _as_int, '800'),
    'auto_update':              ('DEBUG', 'auto_update', _as_bool, 'yes'),
}


@dataclass(frozen=True)
class Config:
    """Configuración efectiva: valores resueltos (default + INI), inmutables."""
    api_key: str
    base_url: str
    model: str
    batch_size: int
    api_call_delay: float
    api_single_timeout: int
    api_batch_timeout: int
    rate_limit_wait_seconds: int
    temperature: float
    max_tokens: int
    enable_translation_cache: bool
    mkvtoolnix_dir: Path | None
    set_new_sub_default: bool
    translated_track_name: str
    output_mkv_suffix: str
    replace_original_mkv: bool
    output_action: str
    reorder_existing_tracks: bool
    target_language_name: str
    target_language_codes: tuple
    preferred_source_lang: str
    latino_keywords: frozenset
    spain_keywords: frozenset
    required_sonarr_tag: str
    prompt_batch_template: str
    prompt_single_template: str
    chapters_enabled: bool
    theme_cache_dir: Path | None
    max_theme_cache_mb: int
    theme_cache_ttl_days: int
    correlation_timeout: int
    score_threshold: int
    snap_tolerance: float
    silence_duration: float
    downsample_factor: int
    theme_portion: float
    anime_path: Path | None
    debug_translation: bool
    debug_max_chars: int
    auto_update: bool

    @property
    def target_language_codes_set(self) -> frozenset:
        """Set de códigos objetivo derivado (para pertenencia O(1))."""
        return frozenset(self.target_language_codes)

    @property
    def primary_target_code(self) -> str:
        """Primer código objetivo (se usa como código MKV de la pista nueva)."""
        return self.target_language_codes[0]


@dataclass(frozen=True)
class FileContext:
    """Contexto por archivo: lo variable entre MKVs, antes inyectado en el dict de config."""
    mkv_path: Path
    series_title: str
    episode_title: str
    season_number: int | None
    chapters_enabled: bool


def load_config(path, capture_warnings: bool = False):
    """
    Carga la Configuración efectiva desde `path`.
    Devuelve (Config, avisos). Los avisos incluyen claves desconocidas y
    valores inválidos corregidos; siempre se registran en el log.
    Si el archivo no existe, se crea con los defaults.
    """
    warnings_out = []

    def _warn(msg):
        """Registra en log Y acumula para quien pida capture_warnings=True."""
        logging.warning("%s", msg)
        warnings_out.append(msg)

    parser = configparser.ConfigParser(interpolation=None)

    # Sembrar defaults (la lectura del usuario pisa por encima)
    seeded = {}
    for _attr, (section, ini_key, _coerce, default) in SPEC.items():
        seeded.setdefault(section, {})[ini_key] = str(default)
    parser.read_dict(seeded)

    path = Path(path)
    if not path.exists():
        logging.warning("config.ini no encontrado. Creando...")
        try:
            with open(path, 'w', encoding='utf-8') as cf:
                parser.write(cf)
            logging.info("Archivo 'config.ini' creado en %s.", path)
        except OSError as e:
            logging.error("No se pudo crear %s: %s", path, e)
    else:
        logging.info("Cargando config: %s", path)
        parser.read(path, encoding='utf-8')
        # Detección de drift: claves del archivo ausentes de la tabla única
        known = {(section, ini_key) for (_a, (section, ini_key, _c, _d)) in SPEC.items()}
        for section in parser.sections():
            for key in parser[section]:
                if (section, key.lower()) not in known:
                    _warn(f"clave_desconocida: [{section}] {key} (ignorada)")

    values = {}
    for attr, (section, ini_key, coerce, default) in SPEC.items():
        raw = parser.get(section, ini_key, fallback=str(default))
        try:
            values[attr] = coerce(raw)
        except (ValueError, TypeError) as e:
            _warn(f"valor inválido para {attr} ({raw!r}): {e}; usando default")
            values[attr] = coerce(str(default))

    # Rutas declaradas que no existen en ESTA máquina: el clásico error de
    # copiar config.ini entre hosts (p. ej. anime_path con unidad de Windows).
    for attr in ('anime_path', 'theme_cache_dir', 'mkvtoolnix_dir'):
        path_value = values[attr]
        if path_value is not None and not Path(path_value).exists():
            _warn(f"ruta_inexistente: {attr} = '{path_value}' "
                  f"(¿copiaste config.ini desde otra máquina?)")

    return Config(**values), warnings_out
