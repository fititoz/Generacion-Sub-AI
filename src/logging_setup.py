"""
logging_setup.py — Logging configuration with file and console handlers.

Sets up dual-output logging: DEBUG level to a rotating log file,
INFO level to stdout console. Called once at application startup.
"""
import logging
import os
import sys
from pathlib import Path
from src.constants import LOG_FILENAME

def setup_logging():
    log_formatter_file = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    log_formatter_console = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    log_level_file = logging.DEBUG
    log_level_console = logging.INFO
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    if logger.hasHandlers():
        logger.handlers.clear()

    log_file_path = Path(__file__).parent.parent / LOG_FILENAME
    os.makedirs(log_file_path.parent, exist_ok=True)

    try:
        file_handler = logging.FileHandler(log_file_path, mode='a', encoding='utf-8')
        file_handler.setFormatter(log_formatter_file)
        file_handler.setLevel(log_level_file)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"Error logging archivo {log_file_path}: {e}", file=sys.stderr)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter_console)
    console_handler.setLevel(log_level_console)
    logger.addHandler(console_handler)
    logging.debug("Logging configurado. Archivo: %s, Consola: %s, Nivel Archivo: %s", log_file_path if 'file_handler' in locals() else "N/A", logging.getLevelName(log_level_console), logging.getLevelName(log_level_file))