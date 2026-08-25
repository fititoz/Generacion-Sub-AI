"""
tag_handler.py — ASS/SSA tag extraction and restoration.

Extracts formatting tags (ASS override blocks, HTML tags, line breaks)
from subtitle text, replacing them with numbered placeholders for safe
translation, then restores them in the translated output.
"""
import re
import logging
from src.constants import TAG_REGEX, LINEBREAK_REGEX, PLACEHOLDER_PREFIX, PLACEHOLDER_SUFFIX

# Literales __TAGn__ preexistentes se escapan a __TAGLn__ durante la
# extracción y se des-escapan al restaurar: así nunca colisionan con los
# placeholders reales (B-TAG) y el modelo tampoco los ve como instrucción.
_LITERAL_TAG_RE = re.compile(
    f"({re.escape(PLACEHOLDER_PREFIX)})(\\d+)({re.escape(PLACEHOLDER_SUFFIX)})")
_LITERAL_ESCAPED_RE = re.compile(
    f"{re.escape(PLACEHOLDER_PREFIX)}L(\\d+){re.escape(PLACEHOLDER_SUFFIX)}")


def _escape_literal_tags(text: str) -> str:
    """Escapa literales __TAGn__ -> __TAGLn__ (no colliden con placeholders)."""
    return _LITERAL_TAG_RE.sub(r"\g<1>L\g<2>\g<3>", text)


def _unescape_literal_tags(text: str) -> str:
    """Operación inversa del escape, al final de restore_tags."""
    return _LITERAL_ESCAPED_RE.sub(f"{PLACEHOLDER_PREFIX}\\g<1>{PLACEHOLDER_SUFFIX}", text)


def extract_tags(text: str):
    """Extrae tags (linebreaks primero, luego ASS/HTML) -> (texto_limpio, tags)."""
    tags = []
    tag_index = 0
    text = _escape_literal_tags(text)

    def replacer(match):
        """Acumula el tag encontrado y devuelve su placeholder numerado."""
        nonlocal tag_index
        tag = match.group(0)
        tags.append(tag)
        placeholder = f"{PLACEHOLDER_PREFIX}{tag_index}{PLACEHOLDER_SUFFIX}"
        tag_index += 1
        return placeholder

    # Primero extraemos saltos de línea sueltos (\N, \n, \h)
    text_with_protected_breaks = LINEBREAK_REGEX.sub(replacer, text)
    
    # Luego extraemos el resto de etiquetas complejas
    cleaned_text = TAG_REGEX.sub(replacer, text_with_protected_breaks)

    if tags:
        logging.debug("[EXTRACT] %d tag(s) extraídos %r de: %.80r", len(tags), tags, text)

    return cleaned_text, tags

def restore_tags(translated_text: str, original_tags: list):
    """Restaura tags por posición; faltantes quedan visibles para el validador."""
    if not original_tags:
        return translated_text

    restored_text = translated_text
    placeholders_found = 0
    placeholders_missing = []

    for i, tag in enumerate(original_tags):
        placeholder = f"{PLACEHOLDER_PREFIX}{i}{PLACEHOLDER_SUFFIX}"
        if placeholder in restored_text:
            restored_text = restored_text.replace(placeholder, tag, 1)
            placeholders_found += 1
        else:
            logging.warning("Placeholder '%s' NO encontrado; devolviendo texto sin restaurar tag: %s", placeholder, tag)
            logging.warning("[RESTORE] TRAD cruda: %r", translated_text)
            placeholders_missing.append(placeholder)
            # NO modificamos el texto: el validador detectará tag count mismatch y forzar re-traducción

    remaining_placeholders_re = re.compile(f"{re.escape(PLACEHOLDER_PREFIX)}\\d+{re.escape(PLACEHOLDER_SUFFIX)}")
    remaining_matches = remaining_placeholders_re.findall(restored_text)
    if remaining_matches:
        logging.warning("Placeholders inesperados DESPUÉS de restaurar: %s", remaining_matches)
    if placeholders_missing:
        logging.warning("Total placeholders no encontrados: %d", len(placeholders_missing))
    elif placeholders_found != len(original_tags) and original_tags:
        logging.warning("Discrepancia conteo placeholders: Esperados=%d, Restaurados=%d", len(original_tags), placeholders_found)

    # Des-escapar literales __TAGLn__ -> __TAGn__ del texto original
    restored_text = _unescape_literal_tags(restored_text)

    return restored_text