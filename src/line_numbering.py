"""
line_numbering.py — Numbered line formatting for batch API communication.

Wraps subtitle lines in [N]: prefix format for API consumption,
parses numbered responses back into a dictionary, and validates that
all expected line indices are present in the response.
"""
import re
import logging

from src.protocol import (
    ALT_NUMBERED_PATTERNS,
    NUMBERING_START,
    NUMBERED_ARTIFACT_RE,
    PRIMARY_NUMBERED_RE,
    format_numbered_line,
)

def add_line_numbers(texts: list[str]) -> str:
    """
    Wraps each line in [N]: prefix for API consumption.
    Example:
    [1]: Hello world
    [2]: Dynamic subtitle
    """
    numbered_lines = []
    for i, text in enumerate(texts, NUMBERING_START):
        # Aseguramos que el texto no tenga saltos de línea internos que rompan el formato
        clean_text = text.replace('\n', ' ')
        numbered_lines.append(format_numbered_line(i, clean_text))
    return "\n".join(numbered_lines)

def parse_numbered_response(response: str, expected_count: int) -> dict[int, str]:
    """
    Parses a response where lines are prefixed with [N]:
    Returns a dictionary mapping 1-based index to translated text.
    
    MEJORA: Soporta múltiples formatos de respuesta de la IA.
    """
    results = {}

    # Patrones canónicos vivos en src/protocol.py (dueño del vocabulario)
    primary_pattern = PRIMARY_NUMBERED_RE

    alt_patterns = ALT_NUMBERED_PATTERNS

    # RECHAZO TEMPRANO: detectar basura tipo "50. Niente correcto." (número + punto + texto sin corchetes)
    # Esto ocurre cuando el modelo ignora el formato [N]: y contesta libre
    if NUMBERED_ARTIFACT_RE.search(response) and not re.search(r'\[\d+\]:', response):
        logging.warning("Respuesta malformada detectada (formato 'N. texto' sin corchetes). Rechazando para forzar fallback.")
        return {}  # Forzar validación a fallar → fallback a translate_single

    # Intentar con el patrón principal primero
    matches = primary_pattern.findall(response)
    pattern_used = "principal [N]:"
    
    # Si el patrón principal no encuentra suficientes resultados, probar alternativos
    if len(matches) < expected_count * 0.5:  # Menos del 50% encontrado
        logging.debug(f"Patrón principal encontró solo {len(matches)}/{expected_count}. Probando alternativos...")
        for pi, alt_pattern in enumerate(alt_patterns):
            alt_matches = alt_pattern.findall(response)
            if len(alt_matches) > len(matches):
                matches = alt_matches
                pattern_used = f"alternativo #{pi + 1}"
                logging.debug(f"Patrón alternativo encontró {len(matches)} resultados.")
                if len(matches) >= expected_count * 0.8:
                    break
    
    duplicates = []
    for index_str, text in matches:
        try:
            idx = int(index_str)
            content = text.strip()
            # Si hay comillas extras alrededor, las quitamos
            if content.startswith('"') and content.endswith('"'): 
                content = content[1:-1]
            # Limpiar saltos de línea internos que no deberían estar
            content = content.replace('\n', ' ').strip()
            if idx in results:
                duplicates.append(idx)  # se conserva la última ocurrencia
            results[idx] = content
        except ValueError:
            continue

    logging.debug("Parseo: patrón_usado=%s, matches=%d, únicos=%d/%d, duplicados=%s",
                  pattern_used, len(matches), len(results), expected_count,
                  sorted(set(duplicates))[:10] if duplicates else "ninguno")
    
    # Log de diagnóstico si hay discrepancia significativa
    if len(results) < expected_count:
        missing = expected_count - len(results)
        logging.warning(f"Parseo incompleto: {len(results)}/{expected_count} líneas recuperadas ({missing} faltantes).")
        if len(results) < expected_count * 0.5:
            logging.debug(f"Respuesta cruda (primeros 500 chars): {response[:500]}")
    
    return results

def validate_response_indices(parsed_results: dict[int, str], expected_count: int) -> list[int]:
    """Returns a list of missing 1-based indices."""
    missing = []
    for i in range(1, expected_count + 1):
        if i not in parsed_results:
            missing.append(i)
    return missing


