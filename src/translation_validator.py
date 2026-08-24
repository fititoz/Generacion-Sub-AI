"""
translation_validator.py — Post-translation quality analysis and correction.

Validates translated subtitles against originals, checking for error markers,
placeholder integrity, untranslated lines, and suspicious length ratios.
Automatically re-translates lines with critical issues via single-line fallback.
"""
import logging
from dataclasses import dataclass
from typing import Optional
from src.tag_handler import restore_tags, extract_tags
from src.protocol import NUMBERED_ARTIFACT_RE, is_error_sentinel

@dataclass
class ValidationResult:
    line_index: int
    original: str
    translated: str
    issues: list[str]
    corrected: Optional[str] = None
    severity: str = 'info' # 'info', 'warning', 'error'

class TranslationValidator:
    def __init__(self, cfg):
        self.cfg = cfg
        # Modo debug: registra original + traducción de cada línea para diagnóstico
        self.debug_translation = cfg.debug_translation

    def validate_all(self, originals: list[str], translations: list[str]) -> list[ValidationResult]:
        if len(originals) != len(translations):
            logging.error(f"Validator mismatch: originals={len(originals)}, translations={len(translations)}")
            return []

        if self.debug_translation:
            logging.debug("=== [DEBUG] INICIO DUMP DE TRADUCCIONES (original -> traducido) ===")

        results = []
        for i, (orig, trans) in enumerate(zip(originals, translations)):
            issues = []

            if self.debug_translation:
                logging.debug(f"[DEBUG] L{i+1} ORIG: {orig!r}")
                logging.debug(f"[DEBUG] L{i+1} TRAD: {trans!r}")
            
            # 1. Verificar marcadores de error
            if is_error_sentinel(trans):
                issues.append(f"Error marker detected: {trans}")
            
            # 2. Verificar integridad de tags (NO placeholders: api_client ya restauró __TAGn__ a tags reales)
            #    Extraemos tags reales de ORIG y TRANS de forma consistente y comparamos conteos.
            cleaned_orig, orig_tags = extract_tags(orig)
            _, trans_tags = extract_tags(trans)

            if len(orig_tags) != len(trans_tags):
                issues.append(f"Tag count mismatch: original={len(orig_tags)}, translation={len(trans_tags)}")
            
            # 3. Verificar si no se tradujo nada (excluyendo líneas que son solo etiquetas)
            if cleaned_orig.strip() and trans.strip() == orig.strip():
                issues.append("Line appears untranslated (identical to original)")

            # 4. Verificar longitud (ratio sospechoso)
            #    Para líneas muy cortas (<=20 chars), ser más permisivos (ratio >= 0.25)
            #    Para líneas normales (>20 chars), threshold estricto (ratio >= 0.3)
            if len(cleaned_orig) > 10 and len(trans) > 0:
                ratio = len(trans) / len(orig)
                threshold = 0.25 if len(cleaned_orig) <= 20 else 0.3
                if ratio < threshold:
                    issues.append(f"Translation suspiciously short (ratio {ratio:.2f})")
                elif ratio > 3.0:
                    issues.append(f"Translation suspiciously long (ratio {ratio:.2f})")

            # 5. Traducción vacía (línea original con contenido pero traducción vacía)
            if cleaned_orig.strip() and not trans.strip():
                issues.append("Translation is empty")

            # 6. Coma huérfana inicial: traducción empieza con ',' pero original no
            if trans.lstrip().startswith(',') and not orig.lstrip().startswith(','):
                issues.append("Leading comma artifact (dropped first word?)")

            # 7. Línea muy corta (≤10 chars) que pierde contenido significativo
            #    Si original >= 3 chars y traducción < 50% del original
            if len(cleaned_orig) >= 3 and len(trans) > 0 and len(cleaned_orig) <= 10:
                ratio = len(trans) / len(orig)
                if ratio < 0.5:
                    issues.append(f"Short line content loss (ratio {ratio:.2f})")

            # 8. Ratio bajo en líneas sin tags (pérdida de contenido moderada)
            #    Solo para líneas > 10 chars (mismo umbral que check principal)
            #    Si no hay tags ASS y ratio < 0.5, flaggear para posible re-traducción
            if len(orig_tags) == 0 and len(trans_tags) == 0 and len(cleaned_orig) > 10 and len(trans) > 0:
                ratio = len(trans) / len(orig)
                if ratio < 0.5:
                    issues.append(f"Low content ratio, no tags (ratio {ratio:.2f})")

            # 9. Artefacto de numeración batch: "N. texto" sin corchetes [N]:
            #    Detecta respuestas crudas del modelo que ignoran el formato solicitado
            if NUMBERED_ARTIFACT_RE.match(trans.strip()):
                issues.append("Batch numbering artifact (raw model response)")

            if issues:
                # CRÍTICO: placeholders mal, error markers, ratio muy bajo, vacía, coma huérfana,
                # línea corta con pérdida, ratio bajo sin tags, artefacto numeración batch
                critical = any(
                    "Error marker" in s or "Placeholder" in s
                    or "suspiciously short" in s
                    or "empty" in s.lower()
                    or "Leading comma" in s
                    or "Short line content loss" in s
                    or "Low content ratio" in s
                    or "Batch numbering artifact" in s
                    for s in issues
                )
                results.append(ValidationResult(
                    line_index=i,
                    original=orig,
                    translated=trans,
                    issues=issues,
                    severity='error' if critical else 'warning'
                ))
                if self.debug_translation:
                    logging.debug("[DEBUG] L%d ISSUES (%s): %s | TRAD: %r",
                                  i + 1, 'error' if critical else 'warning', issues, trans)
        
        return results

class TranslationCorrector:
    def __init__(self, api_client, cache_manager):
        self.api_client = api_client
        self.cache = cache_manager

    def attempt_corrections(self, validation_results: list[ValidationResult], all_translations: list[str]) -> list[str]:
        """
        Intenta corregir resultados con problemas mediante re-traducción.
        NUEVO: Agrupa líneas con error crítico en un mini-batch numerado para 1 sola llamada API.
        Fallback a translate_single solo para líneas que fallen en el batch.
        """
        issues_count = len(validation_results)
        if issues_count == 0:
            return all_translations

        # Filtrar solo errores críticos que requieren re-traducción
        critical_results = [
            res for res in validation_results
            if any("Placeholder" in issue or "Error marker" in issue
                   or "empty" in issue.lower()
                   or "suspiciously short" in issue
                   or "Leading comma" in issue
                   or "Short line content loss" in issue
                   or "Low content ratio" in issue
                   or "Batch numbering artifact" in issue
                   for issue in res.issues)
        ]

        if not critical_results:
            logging.debug(f"{issues_count} líneas con avisos no críticos. Sin re-traducción.")
            return all_translations

        logging.info(f"Re-traduyendo {len(critical_results)} líneas con errores críticos (batch de corrección):")
        for res in critical_results:
            # Mostrar POR QUÉ se re-traduce cada línea: issues + par orig/trad truncado
            orig_preview = res.original[:70] + ('...' if len(res.original) > 70 else '')
            trad_preview = res.translated[:70] + ('...' if len(res.translated) > 70 else '')
            logging.info(f"  L{res.line_index + 1} [{res.severity}] {res.issues}")
            logging.info(f"      ORIG: {orig_preview!r}")
            logging.info(f"      TRAD: {trad_preview!r}")

        # 1. Construir mini-batch con líneas problemáticas + sus índices originales
        error_originals = [res.original for res in critical_results]
        error_indices = [res.line_index for res in critical_results]

        # 2. Enviar como batch numerado único
        try:
            batch_translations = self._translate_error_batch(error_originals)

            # 3. Mapear resultados a índices originales
            for idx, trans in zip(error_indices, batch_translations):
                if trans and trans.strip():
                    all_translations[idx] = trans
                    logging.debug(f"  Línea {idx + 1} corregida via batch: {trans[:60]}...")
                else:
                    logging.warning(f"  Línea {idx + 1}: batch devolvió vacío, fallback a individual")

            # 4. Fallback individual solo para las que el batch dejó vacías
            for i, (idx, orig) in enumerate(zip(error_indices, error_originals)):
                if not all_translations[idx].strip():
                    logging.info(f"  Fallback individual para línea {idx + 1}...")
                    better_trans = self.api_client.translate_single(orig, self.cache)
                    all_translations[idx] = better_trans

        except Exception as e:
            logging.warning(f"Batch de corrección falló ({e}), fallback a individual para todas...")
            for res in critical_results:
                logging.info(f"  Re-traduyendo línea {res.line_index + 1} individualmente...")
                better_trans = self.api_client.translate_single(res.original, self.cache)
                all_translations[res.line_index] = better_trans

        return all_translations

    def _translate_error_batch(self, originals: list[str]) -> list[str]:
        """
        Traduce un batch de líneas con errores usando la API batch.
        Reutiliza la lógica de numeración y parsing del cliente principal.
        """
        if not originals:
            return []

        # Extraer tags y limpiar textos (igual que en translate_batch)
        from src.tag_handler import extract_tags, restore_tags
        cleaned_texts = []
        all_tags = []

        for orig in originals:
            cleaned, tags = extract_tags(orig)
            cleaned_texts.append(cleaned)
            all_tags.append(tags)

        # Usar el método interno de batch del cliente
        # Nota: accedemos a _call_api_batch que ya maneja numeración y parsing
        try:
            translated_cleaned = self.api_client._call_api_batch(cleaned_texts)
        except Exception as e:
            logging.error(f"Error en _call_api_batch de corrección: {e}")
            raise

        # Restaurar tags en cada línea traducida
        final_translations = []
        for i, (cleaned_trans, tags) in enumerate(zip(translated_cleaned, all_tags)):
            if i < len(translated_cleaned):
                restored = restore_tags(cleaned_trans, tags)
                final_translations.append(restored)
            else:
                # Si parse_numbered_response devolvió menos líneas, rellenar con vacío
                final_translations.append("")

        return final_translations
