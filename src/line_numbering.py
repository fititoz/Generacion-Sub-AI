"""
line_numbering.py — Numbered line formatting for batch API communication.

Wraps subtitle lines in [N]: prefix format for Gemini API consumption,
parses numbered responses back into a dictionary, and validates that
all expected line indices are present in the response.
"""
import re
import logging

def add_line_numbers(texts: list[str]) -> str:
    """
    Wraps each line in [N]: prefix for API consumption.
    Example:
    [1]: Hello world
    [2]: Dynamic subtitle
    """
    numbered_lines = []
    for i, text in enumerate(texts, 1):
        # Aseguramos que el texto no tenga saltos de línea internos que rompan el formato
        clean_text = text.replace('\n', ' ')
        numbered_lines.append(f"[{i}]: {clean_text}")
    return "\n".join(numbered_lines)

def parse_numbered_response(response: str, expected_count: int) -> dict[int, str]:
    """
    Parses a response where lines are prefixed with [N]:
    Returns a dictionary mapping 1-based index to translated text.
    
    MEJORA: Soporta múltiples formatos de respuesta de la IA.
    """
    results = {}
    
    # Pattern principal: [N]: texto
    primary_pattern = re.compile(r'\[(\d+)\]:\s*(.*?)(?=\s*\[?\d+\]?:|$)', re.DOTALL)
    
    # Patterns alternativos para respuestas mal formateadas
    alt_patterns = [
        re.compile(r'\[(\d+)\]\s*:\s*(.*?)(?=\s*\[\d+\]|$)', re.DOTALL),  # [N] : texto (espacio antes de :)
        re.compile(r'(\d+)\):\s*(.*?)(?=\s*\d+\)|$)', re.DOTALL),  # N): texto
        re.compile(r'(\d+)\.\s*(.*?)(?=\s*\d+\.|$)', re.DOTALL),  # N. texto
        re.compile(r'^(\d+):\s*(.*?)(?=^\d+:|$)', re.MULTILINE | re.DOTALL),  # N: texto (sin corchetes)
    ]
    
    # Intentar con el patrón principal primero
    matches = primary_pattern.findall(response)
    
    # Si el patrón principal no encuentra suficientes resultados, probar alternativos
    if len(matches) < expected_count * 0.5:  # Menos del 50% encontrado
        logging.debug(f"Patrón principal encontró solo {len(matches)}/{expected_count}. Probando alternativos...")
        for alt_pattern in alt_patterns:
            alt_matches = alt_pattern.findall(response)
            if len(alt_matches) > len(matches):
                matches = alt_matches
                logging.debug(f"Patrón alternativo encontró {len(matches)} resultados.")
                if len(matches) >= expected_count * 0.8:
                    break
    
    for index_str, text in matches:
        try:
            idx = int(index_str)
            content = text.strip()
            # Si hay comillas extras alrededor, las quitamos
            if content.startswith('"') and content.endswith('"'): 
                content = content[1:-1]
            # Limpiar saltos de línea internos que no deberían estar
            content = content.replace('\n', ' ').strip()
            results[idx] = content
        except ValueError:
            continue
    
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


