"""
cache_manager.py — Translation result cache with disk persistence.

Stores original→translated text mappings in a JSON file to avoid
re-translating identical lines across invocations. Supports automatic
pruning when the cache exceeds a configurable maximum size.
"""
import json
import logging
import os
import shutil
from pathlib import Path
from src.constants import CACHE_DIR_NAME, CACHE_FILE_NAME

class TranslationCache:
    def __init__(self, enable_cache: bool, max_entries: int = 10000):
        self.enable_cache = enable_cache
        self.max_entries = max_entries
        self.cache = {}
        if self.enable_cache:
            self._load_cache()
        else:
            logging.info("Caché DESHABILITADO.")

    def _get_cache_path(self):
        script_dir = Path(__file__).parent.parent
        cache_dir = script_dir / CACHE_DIR_NAME
        return cache_dir / CACHE_FILE_NAME

    def _load_cache(self):
        cache_file_path = self._get_cache_path()
        if cache_file_path.exists():
            logging.info(f"Cargando caché: {cache_file_path}")
            try:
                with open(cache_file_path, 'r', encoding='utf-8') as f:
                    self.cache = json.load(f)
                logging.info(f"Caché cargado ({len(self.cache)} entradas).")
            except Exception as e:
                logging.warning(f"No se pudo cargar caché: {e}.", exc_info=True)
        else:
            logging.info("Archivo caché no encontrado.")

    def save_cache(self):
        if not self.enable_cache:
            logging.debug("Guardado caché deshabilitado.");
            return
        if not isinstance(self.cache, dict) or not self.cache:
            logging.info("Caché vacío/inválido, no guardar.");
            return
        cache_file_path = self._get_cache_path()
        try:
            logging.info(f"Guardando caché ({len(self.cache)} entradas) en: {cache_file_path}")
            os.makedirs(cache_file_path.parent, exist_ok=True)
            temp_cache_path = cache_file_path.with_suffix(cache_file_path.suffix + '.tmp')
            with open(temp_cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=4)
            shutil.move(str(temp_cache_path), cache_file_path)
            logging.info("Caché guardado OK.")
        except Exception as e:
            logging.exception(f"Error guardando caché en '{cache_file_path}'.")

    def get(self, key):
        return self.cache.get(key)

    def set(self, key, value):
        if self.enable_cache:
            self.cache[key] = value
            if len(self.cache) > self.max_entries:
                self._prune_cache()

    def _prune_cache(self):
        """Poda el 20% más antiguo de la caché cuando excede max_entries."""
        entries_to_remove = len(self.cache) // 5  # 20%
        if entries_to_remove < 1:
            entries_to_remove = 1
        keys_to_remove = list(self.cache.keys())[:entries_to_remove]
        for key in keys_to_remove:
            del self.cache[key]
        logging.info(f"Caché podada: eliminadas {entries_to_remove} entradas antiguas. "
                     f"Tamaño actual: {len(self.cache)}")

    def __contains__(self, key):
        return key in self.cache