"""
logging_setup.py — Logging configuration with file and console handlers.

Sets up dual-output logging: DEBUG level to a rotating log file,
INFO level to stdout console. Called once at application startup.
Third-party SDK noise (httpx, openai, httpcore) is silenced to WARNING
so each API call does not emit a log line of its own.
"""
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
from src.constants import LOG_FILENAME

def setup_logging(debug_mode: bool = False):
    log_formatter_file = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    log_formatter_console = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # En modo debug el archivo captura DEBUG (traza por etapa); consola siempre INFO.
    log_level_file = logging.DEBUG if debug_mode else logging.INFO
    log_level_console = logging.INFO
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG if debug_mode else logging.INFO)

    if logger.hasHandlers():
        logger.handlers.clear()

    # Silenciar ruido de SDKs de terceros: 1 línea "HTTP Request ... 200 OK"
    # por cada llamada API satura el log en lotes grandes.
    for noisy in ('httpx', 'httpcore', 'openai', 'openai._client', 'openai._base_client'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    class _SdkTraceFilter(logging.Filter):
        """Corta trazas internas del SDK (p. ej. _trace.py close.started/complete)
        pase lo que pase con el nombre del logger emisor."""
        def filter(self, record):
            return record.filename != "_trace.py"

    sdk_trace_filter = _SdkTraceFilter()

    log_file_path = Path(__file__).parent.parent / LOG_FILENAME
    os.makedirs(log_file_path.parent, exist_ok=True)

    try:
        file_handler = RotatingFileHandler(log_file_path, mode='a', maxBytes=5242880, backupCount=2, encoding='utf-8')
        file_handler.setFormatter(log_formatter_file)
        file_handler.setLevel(log_level_file)
        file_handler.addFilter(sdk_trace_filter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"Error logging archivo {log_file_path}: {e}", file=sys.stderr)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter_console)
    console_handler.setLevel(log_level_console)
    console_handler.addFilter(sdk_trace_filter)
    logger.addHandler(console_handler)
    logging.debug("Logging configurado. Archivo: %s, Nivel archivo: %s, Nivel consola: %s, Debug: %s",
                  log_file_path if 'file_handler' in locals() else "N/A",
                  logging.getLevelName(log_level_file), logging.getLevelName(log_level_console),
                  debug_mode)
