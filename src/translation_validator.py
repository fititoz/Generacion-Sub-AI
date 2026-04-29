"""
translation_validator.py — Post-translation quality analysis and correction.

Validates translated subtitles against originals, checking for error markers,
placeholder integrity, untranslated lines, and suspicious length ratios.
Automatically re-translates lines with critical issues via single-line fallback.
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional
from src.tag_handler import restore_tags, extract_tags
from src.constants import PLACEHOLDER_PREFIX, PLACEHOLDER_SUFFIX

@dataclass
class ValidationResult:
    line_index: int
    original: str
    translated: str
    issues: list[str]
    corrected: Optional[str] = None
    severity: str = 'info' # 'info', 'warning', 'error'

class TranslationValidator:
    def __init__(self, config: dict):
        self.config = config

    def validate_all(self, originals: list[str], translations: list[str]) -> list[ValidationResult]:
        if len(originals) != len(translations):
            logging.error(f"Validator mismatch: originals={len(originals)}, translations={len(translations)}")
            return []

        results = []
        for i, (orig, trans) in enumerate(zip(originals, translations)):
            issues = []
            
            # 1. Verificar marcadores de error
            if trans.startswith("[[") and trans.endswith("]]"):
                issues.append(f"Error marker detected: {trans}")
            
            # 2. Verificar integridad de placeholders
            cleaned_orig, orig_tags = extract_tags(orig)
            placeholder_pattern = re.compile(f"{re.escape(PLACEHOLDER_PREFIX)}\\d+{re.escape(PLACEHOLDER_SUFFIX)}")
            trans_placeholders = placeholder_pattern.findall(trans)
            
            if len(trans_placeholders) != len(orig_tags):
                issues.append(f"Placeholder mismatch: expected {len(orig_tags)}, found {len(trans_placeholders)}")
            
            # 3. Verificar si no se tradujo nada (excluyendo líneas que son solo etiquetas)
            if cleaned_orig.strip() and trans.strip() == orig.strip():
                issues.append("Line appears untranslated (identical to original)")

            # 4. Verificar longitud (ratio sospechoso)
            if len(cleaned_orig) > 10 and len(trans) > 0:
                ratio = len(trans) / len(orig)
                if ratio < 0.3:
                    issues.append(f"Translation suspiciously short (ratio {ratio:.2f})")
                elif ratio > 3.0:
                    issues.append(f"Translation suspiciously long (ratio {ratio:.2f})")

            if issues:
                results.append(ValidationResult(
                    line_index=i,
                    original=orig,
                    translated=trans,
                    issues=issues,
                    severity='error' if any("Error marker" in s or "Placeholder" in s for s in issues) else 'warning'
                ))
        
        return results

class TranslationCorrector:
    def __init__(self, gemini_client, cache_manager):
        self.gemini_client = gemini_client
        self.cache = cache_manager

    def attempt_corrections(self, validation_results: list[ValidationResult], all_translations: list[str]) -> list[str]:
        """
        Intenta corregir resultados con problemas mediante re-traducción individual.
        """
        issues_count = len(validation_results)
        if issues_count == 0:
            return all_translations

        logging.info(f"Intentando corregir {issues_count} líneas con problemas...")
        
        for res in validation_results:
            # Si el error es crítico (placeholders mal o marcador de error)
            critical_issue = any("Placeholder" in issue or "Error marker" in issue for issue in res.issues)
            
            if critical_issue:
                logging.info(f"Re-traduciendo línea {res.line_index + 1} individualmente por error crítico...")
                better_trans = self.gemini_client.translate_single_gemini(res.original, self.cache)
                all_translations[res.line_index] = better_trans
            else:
                logging.debug(f"Línea {res.line_index + 1} marcada con avisos: {', '.join(res.issues)}. No se requiere acción crítica.")
                
        return all_translations
