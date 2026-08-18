"""
config_manager.py — Configuration file parser and validator.

Reads config.ini, applies defaults, validates values, and exposes
a flat dictionary of typed configuration parameters.
Sections: [API], [LLM_SETTINGS], [MKV_OUTPUT], [PROMPTS], [CHAPTERS], [DEBUG]
"""
import configparser
import logging
from pathlib import Path


class ConfigManager:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = configparser.ConfigParser(interpolation=None)
        self._load_defaults()
        self._load_config_file()
        self.cfg = {}
        self._parse_config()

    def _load_defaults(self):
        default_batch_prompt = """Contexto: Estás traduciendo el anime "{series_title}" - Episodio: "{episode_title}".
    Eres un traductor experto de subtítulos especializado en anime. Traduce las siguientes {batch_size_info} líneas de subtítulos al {target_language_name}, siguiendo estas reglas ESTRICTAMENTE:
    1.  **Idioma y Tono**: Usa {target_language_name} natural y sin censura. No traduzcas si el texto ya está en español o si es irrelevante. Tuteo por defecto (voseo si aplica a la región).
    2.  **Preservación**: Mantén SIEMPRE intactos: Nombres propios, términos específicos (Oshiruko, Kunai, etc.), honoríficos japoneses (-san, -chan, -sama).
    3.  **Formato de Entrada/Salida**: Cada línea viene numerada con el formato [N]: Texto.
    4.  **DEBES responder manteniendo EXACTAMENTE el mismo formato: [N]: Traducción.**
    5.  **Placeholders**: Preserva etiquetas como __TAG0__, __TAG1__ EXACTAMENTE en su posición relativa.
    6.  **Ortografía y Puntuación**: Usa ortografía impecable. Incluye signos de apertura (¿, ¡), tildes correctas y la letra ñ. Evita modismos regionales excesivos a menos que sea necesario para el tono.
    7.  **Consistencia**: Responde ÚNICAMENTE con las líneas traducidas numeradas. No agregues preámbulos ni explicaciones.

    Líneas a traducir:
    {batch_text}

    Traducciones:"""

        default_single_prompt = """Contexto: {series_title} - {episode_title}.
    Eres un traductor experto de subtítulos especializado en anime. Traduce la siguiente línea al {target_language_name}:
    
    Texto original:
    {text}
    
    Traducción:"""

        defaults = {
            'API': {
                'api_key': 'TU_API_KEY_AQUI',
                'base_url': 'http://localhost:20128/v1'
            },
            'LLM_SETTINGS': {
                'model': 'auto/best-free',
                'batch_size': '50',
                'api_call_delay': '5.0',
                'api_max_retries': '3',
                'api_retry_initial_delay': '20',
                'api_single_timeout': '120',
                'api_batch_timeout': '300',
                'rate_limit_wait_seconds': '60',
                'rate_limit_max_global_retries': '3',
                'temperature': '0.3',
                'max_tokens': '2000',
                'enable_translation_cache': 'yes'
            },
            'MKV_OUTPUT': {
                'mkvtoolnix_dir': '',
                'add_subs_to_mkv': 'yes',
                'set_new_sub_default': 'yes',
                'translated_track_name': 'Español Latino (AI)',
                'output_mkv_suffix': '.traducido',
                'replace_original_mkv': 'no',
                'output_action': 'save_separate_sub',
                'reorder_existing_tracks': 'yes',
                'target_language_name': 'Español Latino (sin censura)',
                'target_language_codes': 'es-419, lat, es, spa, und',
                'preferred_source_lang': 'fre',
                'latino_keywords': 'latino, latin, latam, latin america, español americano, animo, Spanish[LAT]',
                'spain_keywords': 'españa, spain, castellano, castilian, español europeo, iberian, european, Spanish[ESP]',
                'required_sonarr_tag': ''
            },
            'PROMPTS': {
                'batch_template': default_batch_prompt,
                'single_template': default_single_prompt
            },
            'CHAPTERS': {
                'enabled': 'yes',
                'theme_cache_dir': '/config/scripts/OPED',
                'max_theme_cache_mb': '1024',
                'theme_cache_ttl_days': '120',
                'correlation_timeout': '120',
                'score_threshold': '2000',
                'snap_tolerance': '4.0',
                'silence_duration': '5.0',
                'downsample_factor': '32',
                'anime_path': '/home/biblioteca/Anime'
            },
            'DEBUG': {
                'debug_translation': 'true',
                'auto_update': 'yes'
            }
        }
        self.config.read_dict(defaults)

    def _load_config_file(self):
        if not self.config_path.exists():
            logging.warning("config.ini no encontrado. Creando...");
            try:
                with open(self.config_path, 'w', encoding='utf-8') as cf:
                    self.config.write(cf)
                logging.info(f"Archivo 'config.ini' creado en {self.config_path}.")
            except OSError as e:
                logging.error(f"No se pudo crear {self.config_path}: {e}")
        else:
            logging.info(f"Cargando config: {self.config_path}");
            self.config.read(self.config_path, encoding='utf-8')

    def _parse_config(self):
        # [API] section
        self.cfg['API_KEY'] = self.config.get('API', 'api_key')
        self.cfg['BASE_URL'] = self.config.get('API', 'base_url', fallback='http://localhost:20128/v1')

        # [LLM_SETTINGS] section
        self.cfg['MODEL'] = self.config.get('LLM_SETTINGS', 'model', fallback='auto/best-free')
        self.cfg['BATCH_SIZE'] = self.config.getint('LLM_SETTINGS', 'batch_size', fallback=50)
        self.cfg['API_CALL_DELAY'] = self.config.getfloat('LLM_SETTINGS', 'api_call_delay', fallback=5.0)
        self.cfg['API_MAX_RETRIES'] = self.config.getint('LLM_SETTINGS', 'api_max_retries', fallback=3)
        self.cfg['API_RETRY_INITIAL_DELAY'] = self.config.getint('LLM_SETTINGS', 'api_retry_initial_delay', fallback=20)
        self.cfg['API_SINGLE_TIMEOUT'] = self.config.getint('LLM_SETTINGS', 'api_single_timeout', fallback=120)
        self.cfg['API_BATCH_TIMEOUT'] = self.config.getint('LLM_SETTINGS', 'api_batch_timeout', fallback=300)
        self.cfg['RATE_LIMIT_WAIT_SECONDS'] = self.config.getint('LLM_SETTINGS', 'rate_limit_wait_seconds', fallback=60)
        self.cfg['RATE_LIMIT_MAX_GLOBAL_RETRIES'] = self.config.getint('LLM_SETTINGS', 'rate_limit_max_global_retries', fallback=3)
        self.cfg['TEMPERATURE'] = self.config.getfloat('LLM_SETTINGS', 'temperature', fallback=0.3)
        self.cfg['MAX_TOKENS'] = self.config.getint('LLM_SETTINGS', 'max_tokens', fallback=2000)
        self.cfg['ENABLE_TRANSLATION_CACHE'] = self.config.getboolean('LLM_SETTINGS', 'enable_translation_cache', fallback=True)

        # [MKV_OUTPUT] section
        mkvtoolnix_dir = self.config.get('MKV_OUTPUT', 'mkvtoolnix_dir', fallback='').strip()
        self.cfg['MKVTOOLNIX_DIR'] = mkvtoolnix_dir if mkvtoolnix_dir else None

        self.cfg['ADD_SUBS_TO_MKV'] = self.config.getboolean('MKV_OUTPUT', 'add_subs_to_mkv', fallback=True)
        self.cfg['SET_NEW_SUB_DEFAULT'] = self.config.getboolean('MKV_OUTPUT', 'set_new_sub_default', fallback=True)
        raw_track_name = self.config.get('MKV_OUTPUT', 'translated_track_name', fallback='Español Latino (AI)')
        self.cfg['TRANSLATED_TRACK_NAME'] = raw_track_name
        self.cfg['OUTPUT_MKV_SUFFIX'] = self.config.get('MKV_OUTPUT', 'output_mkv_suffix', fallback='.traducido')
        self.cfg['REPLACE_ORIGINAL_MKV'] = self.config.getboolean('MKV_OUTPUT', 'replace_original_mkv', fallback=False)
        self.cfg['OUTPUT_ACTION'] = self.config.get('MKV_OUTPUT', 'output_action', fallback='save_separate_sub').strip().lower()
        if self.cfg['OUTPUT_ACTION'] not in ['remux', 'save_separate_sub']:
            logging.warning(f"output_action inválido '{self.cfg['OUTPUT_ACTION']}'. Usando 'save_separate_sub'.");
            self.cfg['OUTPUT_ACTION'] = 'save_separate_sub'
        logging.info(f"Acción de salida: {self.cfg['OUTPUT_ACTION']}")

        self.cfg['REORDER_EXISTING_TRACKS'] = self.config.getboolean('MKV_OUTPUT', 'reorder_existing_tracks', fallback=True)

        # Language settings from MKV_OUTPUT
        self.cfg['TARGET_LANGUAGE_NAME'] = self.config.get('MKV_OUTPUT', 'target_language_name', fallback='Español Latino (sin censura)')
        target_codes_str = self.config.get('MKV_OUTPUT', 'target_language_codes', fallback='es-419, lat, es, spa, und')
        self.cfg['TARGET_LANGUAGE_CODES_LIST'] = [c.strip().lower() for c in target_codes_str.split(',') if c.strip()] or ['spa']
        self.cfg['TARGET_LANGUAGE_CODES_SET'] = set(self.cfg['TARGET_LANGUAGE_CODES_LIST'])
        self.cfg['PRIMARY_TARGET_CODE'] = self.cfg['TARGET_LANGUAGE_CODES_LIST'][0]
        self.cfg['PREFERRED_SOURCE_LANG'] = self.config.get('MKV_OUTPUT', 'preferred_source_lang', fallback='fre')

        latino_kw_str = self.config.get('MKV_OUTPUT', 'latino_keywords', fallback='latino, latin, latam, latin america, español americano, animo, Spanish[LAT]')
        spain_kw_str = self.config.get('MKV_OUTPUT', 'spain_keywords', fallback='españa, spain, castellano, castilian, español europeo, iberian, european, Spanish[ESP]')
        self.cfg['LATINO_KEYWORDS'] = {kw.strip().lower() for kw in latino_kw_str.split(',') if kw.strip()}
        self.cfg['SPAIN_KEYWORDS'] = {kw.strip().lower() for kw in spain_kw_str.split(',') if kw.strip()}

        self.cfg['REQUIRED_SONARR_TAG'] = self.config.get('MKV_OUTPUT', 'required_sonarr_tag', fallback='').strip()

        # [PROMPTS] section
        self.cfg['BATCH_TRANSLATION_PROMPT_TEMPLATE'] = self.config.get('PROMPTS', 'batch_template').strip()
        self.cfg['SINGLE_TRANSLATION_PROMPT_TEMPLATE'] = self.config.get('PROMPTS', 'single_template').strip()

        # [CHAPTERS] section
        self.cfg['CHAPTERS_ENABLED'] = self.config.getboolean('CHAPTERS', 'enabled', fallback=False)
        theme_cache_raw = self.config.get('CHAPTERS', 'theme_cache_dir', fallback='').strip()
        self.cfg['CHAPTERS_THEME_CACHE_DIR'] = Path(theme_cache_raw) if theme_cache_raw else None
        if self.cfg['CHAPTERS_THEME_CACHE_DIR'] and not self.cfg['CHAPTERS_THEME_CACHE_DIR'].exists():
            logging.warning("Directorio caché de temas no existe: %s (se creará al primer uso)", self.cfg['CHAPTERS_THEME_CACHE_DIR'])
        self.cfg['MAX_THEME_CACHE_MB'] = self.config.getint('CHAPTERS', 'max_theme_cache_mb', fallback=1024)
        self.cfg['THEME_CACHE_TTL_DAYS'] = self.config.getint('CHAPTERS', 'theme_cache_ttl_days', fallback=120)
        self.cfg['CORRELATION_TIMEOUT'] = self.config.getint('CHAPTERS', 'correlation_timeout', fallback=120)
        self.cfg['SCORE_THRESHOLD'] = self.config.getint('CHAPTERS', 'score_threshold', fallback=2000)
        self.cfg['SNAP_TOLERANCE'] = self.config.getfloat('CHAPTERS', 'snap_tolerance', fallback=4.0)
        self.cfg['SILENCE_DURATION'] = self.config.getfloat('CHAPTERS', 'silence_duration', fallback=5.0)
        self.cfg['DOWNSAMPLE_FACTOR'] = self.config.getint('CHAPTERS', 'downsample_factor', fallback=32)
        anime_path_raw = self.config.get('CHAPTERS', 'anime_path', fallback='').strip()
        self.cfg['ANIME_PATH'] = Path(anime_path_raw) if anime_path_raw else None

        # [DEBUG] section
        self.cfg['DEBUG_TRANSLATION'] = self.config.getboolean('DEBUG', 'debug_translation', fallback=False)
        self.cfg['AUTO_UPDATE'] = self.config.getboolean('DEBUG', 'auto_update', fallback=True)

    def get_all(self) -> dict:
        """Returns the full flat configuration dictionary."""
        return self.cfg

    def get(self, key: str, default=None):
        """Get a single config value by key."""
        return self.cfg.get(key, default)