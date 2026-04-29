"""
tag_handler.py — ASS/SSA tag extraction and restoration.

Extracts formatting tags (ASS override blocks, HTML tags, line breaks)
from subtitle text, replacing them with numbered placeholders for safe
translation, then restores them in the translated output.
"""
import re
import logging
from src.constants import TAG_REGEX, LINEBREAK_REGEX, PLACEHOLDER_PREFIX, PLACEHOLDER_SUFFIX

def extract_tags(text: str):
    tags = []
    tag_index = 0

    def replacer(match):
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
    
    return cleaned_text, tags

def restore_tags(translated_text: str, original_tags: list):
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
            logging.warning("Placeholder '%s' NO encontrado...", placeholder)
            placeholders_missing.append(placeholder)

    remaining_placeholders_re = re.compile(f"{re.escape(PLACEHOLDER_PREFIX)}\\d+{re.escape(PLACEHOLDER_SUFFIX)}")
    remaining_matches = remaining_placeholders_re.findall(restored_text)
    if remaining_matches:
        logging.warning("Placeholders inesperados DESPUÉS de restaurar: %s", remaining_matches)
    if placeholders_missing:
        logging.warning("Total placeholders no encontrados: %d", len(placeholders_missing))
    elif placeholders_found != len(original_tags) and original_tags:
        logging.warning("Discrepancia conteo placeholders: Esperados=%d, Restaurados=%d", len(original_tags), placeholders_found)

    return restored_text