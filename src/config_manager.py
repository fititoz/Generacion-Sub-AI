"""
config_manager.py — Configuration file parser and validator.

Reads config.ini, applies defaults, validates values, and exposes
a flat dictionary of typed configuration parameters.
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
        default_batch_prompt = """(Prompt batch con __TAGn__)"""
        default_single_prompt = """(Prompt single con __TAGn__)"""
        defaults = {
            'API': {'gemini_api_key': 'TU_API_KEY_AQUI'},
            'PATHS': {'mkvtoolnix_dir': ''},
            'TRANSLATION': {
                'target_language_name': 'Español Latino (sin censura)',
                'target_language_codes': 'es-419, spa, es, lat',
                'preferred_source_lang': 'eng',
                'preferred_models': '\n'.join(['gemini-1.5-pro-latest', 'gemini-1.5-flash-latest']),
                'batch_size': '20',
                'api_call_delay': '5.0',
                'api_max_retries': '3',
                'api_retry_initial_delay': '2',
                'api_single_timeout': '120',
                'api_batch_timeout': '300',
                'rate_limit_wait_seconds': '60',
                'rate_limit_max_global_retries': '3',
                'latino_keywords': 'latino, latin, latam, español americano',
                'spain_keywords': 'españa, spain, castellano, castilian, español europeo, iberian'
            },
            'SETTINGS': {
                'output_action': 'remux',
                'add_subs_to_mkv': 'yes',
                'set_new_sub_default': 'no',
                'translated_track_name': '{lang_name} (Gemini v18)',
                'output_mkv_suffix': '.traducido',
                'enable_translation_cache': 'yes',
                'replace_original_mkv': 'no'
            },
            'CHAPTERS': {
                'enabled': 'no',
                'theme_cache_dir': '',
                'correlation_timeout': '120',
                'score_threshold': '2000',
                'snap_tolerance': '4.0',
                'silence_duration': '5.0',
                'downsample_factor': '32',
                'anime_path': '',
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
        try:
            self.cfg['GEMINI_API_KEY'] = self.config.get('API', 'gemini_api_key')
            self.cfg['MKVTOOLNIX_DIR'] = self.config.get('PATHS', 'mkvtoolnix_dir') or None
            self.cfg['TARGET_LANGUAGE_NAME'] = self.config.get('TRANSLATION', 'target_language_name')
            target_codes_str = self.config.get('TRANSLATION', 'target_language_codes')
            self.cfg['TARGET_LANGUAGE_CODES_LIST'] = [c.strip().lower() for c in target_codes_str.split(',') if c.strip()] or ['spa']
            self.cfg['TARGET_LANGUAGE_CODES_SET'] = set(self.cfg['TARGET_LANGUAGE_CODES_LIST'])
            self.cfg['PRIMARY_TARGET_CODE'] = self.cfg['TARGET_LANGUAGE_CODES_LIST'][0]
            self.cfg['PREFERRED_SOURCE_LANG'] = self.config.get('TRANSLATION', 'preferred_source_lang')
            preferred_models_str = self.config.get('TRANSLATION', 'preferred_models')
            self.cfg['PREFERRED_MODELS'] = [model.strip() for model in preferred_models_str.splitlines() if model.strip() and not model.strip().startswith('#')]
            self.cfg['BATCH_SIZE'] = self.config.getint('TRANSLATION', 'batch_size')
            self.cfg['API_CALL_DELAY'] = self.config.getfloat('TRANSLATION', 'api_call_delay')
            self.cfg['API_MAX_RETRIES'] = self.config.getint('TRANSLATION', 'api_max_retries')
            self.cfg['API_RETRY_INITIAL_DELAY'] = self.config.getint('TRANSLATION', 'api_retry_initial_delay')
            self.cfg['API_SINGLE_TIMEOUT'] = self.config.getint('TRANSLATION', 'api_single_timeout')
            self.cfg['API_BATCH_TIMEOUT'] = self.config.getint('TRANSLATION', 'api_batch_timeout')
            self.cfg['RATE_LIMIT_WAIT_SECONDS'] = self.config.getint('TRANSLATION', 'rate_limit_wait_seconds')
            self.cfg['RATE_LIMIT_MAX_GLOBAL_RETRIES'] = self.config.getint('TRANSLATION', 'rate_limit_max_global_retries')
            latino_kw_str = self.config.get('TRANSLATION', 'latino_keywords')
            spain_kw_str = self.config.get('TRANSLATION', 'spain_keywords')
            self.cfg['LATINO_KEYWORDS'] = {kw.strip().lower() for kw in latino_kw_str.split(',') if kw.strip()}
            self.cfg['SPAIN_KEYWORDS'] = {kw.strip().lower() for kw in spain_kw_str.split(',') if kw.strip()}
            self.cfg['OUTPUT_ACTION'] = self.config.get('SETTINGS', 'output_action').strip().lower()
            if self.cfg['OUTPUT_ACTION'] not in ['remux', 'save_separate_sub']:
                logging.warning(f"output_action inválido '{self.cfg['OUTPUT_ACTION']}'. Usando 'remux'.");
                self.cfg['OUTPUT_ACTION'] = 'remux'
            logging.info(f"Acción de salida: {self.cfg['OUTPUT_ACTION']}")
            self.cfg['ADD_SUBS_TO_MKV'] = self.config.getboolean('SETTINGS', 'add_subs_to_mkv')
            self.cfg['SET_NEW_SUB_DEFAULT'] = self.config.getboolean('SETTINGS', 'set_new_sub_default')
            raw_track_name = self.config.get('SETTINGS', 'translated_track_name')
            self.cfg['TRANSLATED_TRACK_NAME'] = raw_track_name.format(lang_name=self.cfg['TARGET_LANGUAGE_NAME'])
            self.cfg['OUTPUT_MKV_SUFFIX'] = self.config.get('SETTINGS', 'output_mkv_suffix')
            self.cfg['ENABLE_TRANSLATION_CACHE'] = self.config.getboolean('SETTINGS', 'enable_translation_cache')
            self.cfg['REPLACE_ORIGINAL_MKV'] = self.config.getboolean('SETTINGS', 'replace_original_mkv')
            self.cfg['REORDER_EXISTING_TRACKS'] = self.config.getboolean('SETTINGS', 'reorder_existing_tracks', fallback=True)
            self.cfg['BATCH_TRANSLATION_PROMPT_TEMPLATE'] = self.config.get('PROMPTS', 'batch_template').strip()
            self.cfg['SINGLE_TRANSLATION_PROMPT_TEMPLATE'] = self.config.get('PROMPTS', 'single_template').strip()

            # --- [CHAPTERS] section (all with fallback= for backward compatibility) ---
            self.cfg['CHAPTERS_ENABLED'] = self.config.getboolean('CHAPTERS', 'enabled', fallback=False)
            theme_cache_raw = self.config.get('CHAPTERS', 'theme_cache_dir', fallback='').strip()
            self.cfg['CHAPTERS_THEME_CACHE_DIR'] = Path(theme_cache_raw) if theme_cache_raw else None
            if self.cfg['CHAPTERS_THEME_CACHE_DIR'] and not self.cfg['CHAPTERS_THEME_CACHE_DIR'].exists():
                logging.warning("Directorio caché de temas no existe: %s (se creará al primer uso)", self.cfg['CHAPTERS_THEME_CACHE_DIR'])
            self.cfg['CORRELATION_TIMEOUT'] = self.config.getint('CHAPTERS', 'correlation_timeout', fallback=120)
            self.cfg['SCORE_THRESHOLD'] = self.config.getint('CHAPTERS', 'score_threshold', fallback=2000)
            self.cfg['SNAP_TOLERANCE'] = self.config.getfloat('CHAPTERS', 'snap_tolerance', fallback=4.0)
            self.cfg['SILENCE_DURATION'] = self.config.getfloat('CHAPTERS', 'silence_duration', fallback=5.0)
            self.cfg['DOWNSAMPLE_FACTOR'] = self.config.getint('CHAPTERS', 'downsample_factor', fallback=32)
            anime_path_raw = self.config.get('CHAPTERS', 'anime_path', fallback='').strip()
            self.cfg['CHAPTERS_ANIME_PATH'] = Path(anime_path_raw) if anime_path_raw else None
            if self.cfg['CHAPTERS_ANIME_PATH']:
                logging.info("Capítulos limitados a ruta: %s", self.cfg['CHAPTERS_ANIME_PATH'])
            logging.debug("Configuración parseada OK.")
        except Exception as e:
            logging.error(f"Error parseando 'config.ini': {e}", exc_info=True)
            raise SystemExit("Config inválida.")

    def get(self, key):
        return self.cfg.get(key)

    def get_all(self):
        return self.cfg