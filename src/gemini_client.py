"""
gemini_client.py — Google Gemini API client for subtitle translation.

Handles API communication, batch/single translation, recursive fallback
on partial failures, rate limit detection, and automatic model rotation.
"""
import logging
import time
import math
from google import genai
from google.genai import types
from src.tag_handler import extract_tags, restore_tags
from src.cache_manager import TranslationCache
from src.exceptions import (
    APIConnectionError, APIResponseError, LineCountMismatchError, 
    ContentBlockedError, TranslationTimeoutError
)
from src.line_numbering import add_line_numbers, parse_numbered_response, validate_response_indices
from src.model_manager import ModelManager

class GeminiClient:
    def __init__(self, api_key: str, config: dict):
        self.api_key = api_key
        self.config = config
        self.client = None
        self.model_manager = None  # Se inicializará después de obtener modelos de la API
        self._configure_gemini()

    @property
    def current_model_name(self) -> str:
        if self.model_manager:
            return self.model_manager.get_current_model() or "Ninguno disponible"
        return "No inicializado"

    def _configure_gemini(self):
        try:
            # Crear cliente con la nueva API
            self.client = genai.Client(api_key=self.api_key)
            logging.info("Obteniendo modelos API...")
            
            # Listar modelos disponibles
            all_models = list(self.client.models.list())
            all_suitable_models = [(m.name, m.display_name) for m in all_models 
                                   if hasattr(m, 'supported_actions') and 'generate_content' in str(m.supported_actions).lower() 
                                   or 'gemini' in m.name.lower()]
            
            if not all_suitable_models:
                all_suitable_models = [(m.name, m.display_name) for m in all_models if 'gemini' in m.name.lower()]
            
            if not all_suitable_models:
                raise SystemExit("No hay modelos compatibles.")
            
            # MEJORA: Crear ModelManager con validación contra modelos de la API
            api_model_names = [name for name, _ in all_suitable_models]
            self.model_manager = ModelManager(
                self.config.get('PREFERRED_MODELS', []),
                available_api_models=api_model_names
            )
            
            # Bucle para encontrar un modelo que funcione
            while True:
                current_model = self.model_manager.get_current_model()
                if current_model is None:
                    raise SystemExit("Ningún modelo disponible respondió al test inicial.")
                
                logging.info("Probando conexión con modelo '%s'...", current_model)
                
                try:
                    # Test de conexión con safety settings desactivados
                    test_response = self.client.models.generate_content(
                        model=current_model,
                        contents='Test',
                        config=types.GenerateContentConfig(
                            safety_settings=self._get_safety_settings(),
                            http_options=types.HttpOptions(timeout=30 * 1000)
                        ),
                    )
                    
                    if not test_response.candidates:
                        logging.warning(f"Modelo {current_model} no devolvió respuesta en test. Intentando siguiente...")
                        if not self.model_manager.switch_to_next_model():
                             break
                        continue
                        
                    logging.info("Conexión Gemini OK con modelo '%s'.", current_model)
                    break # Éxito
                    
                except Exception as e:
                    if self._is_rate_limit_error(e):
                        logging.warning(f"Rate limit detectado en modelo '{current_model}' durante el test inicial.")
                        self.model_manager.report_rate_limit(current_model)
                    elif self._is_retryable_error(e):
                        logging.warning(f"Error reintentable en modelo '{current_model}' durante el test: {e}")
                    else:
                        logging.error(f"Error fatal probando modelo '{current_model}': {e}")
                    
                    if not self.model_manager.switch_to_next_model():
                        raise SystemExit("Todos los modelos fallaron o están agotados.")
                    logging.info("Intentando con el siguiente modelo disponible...")

        except SystemExit:
            raise
        except Exception as e:
            logging.exception("Error critico en configuración Gemini:")
            raise SystemExit(f"Fallo conexión Gemini: {e}")

    def _get_safety_settings(self):
        """Retorna la configuración de seguridad desactivada para traducciones."""
        return [
            types.SafetySetting(category='HARM_CATEGORY_HARASSMENT', threshold='BLOCK_NONE'),
            types.SafetySetting(category='HARM_CATEGORY_HATE_SPEECH', threshold='BLOCK_NONE'),
            types.SafetySetting(category='HARM_CATEGORY_SEXUALLY_EXPLICIT', threshold='BLOCK_NONE'),
            types.SafetySetting(category='HARM_CATEGORY_DANGEROUS_CONTENT', threshold='BLOCK_NONE'),
        ]

    def _is_rate_limit_error(self, exception):
        """Detecta específicamente errores 429 / Resource Exhausted."""
        error_str = str(exception).lower()
        return '429' in error_str or 'exhausted' in error_str or 'too many requests' in error_str

    def _is_retryable_error(self, exception):
        """Determina si el error es recuperable mediante reintento (incluye rate limit)."""
        error_str = str(exception).lower()
        retryable_patterns = ['retry', 'internal', 'unavailable', 'deadline', 'timeout', '503', '500']
        return any(pattern in error_str for pattern in retryable_patterns) or self._is_rate_limit_error(exception)

    def _handle_api_response(self, response, is_batch: bool = False):
        if not response.candidates:
            reason = getattr(response, 'prompt_feedback', '?')
            logging.warning(f"API {'Batch' if is_batch else 'Single'} Bloqueada (Razón: %s).", reason)
            raise ContentBlockedError(str(reason))
        
        candidate = response.candidates[0]
        finish_reason = getattr(candidate, 'finish_reason', 'UNKNOWN')

        if candidate.content and candidate.content.parts:
            try:
                api_result_full = response.text.strip()
            except (ValueError, AttributeError):
                logging.warning(f"API {'Batch' if is_batch else 'Single'}: Error al acceder a response.text, reconstruyendo desde partes.")
                api_result_full = "".join(part.text for part in candidate.content.parts if hasattr(part, 'text')).strip()
        else:
            logging.warning(f"API {'Batch' if is_batch else 'Single'}: La API no devolvió contenido. Razón: {finish_reason}.")
            api_result_full = ""
        
        if str(finish_reason) == "MAX_TOKENS":
            logging.warning(f"API {'Batch' if is_batch else 'Single'}: La respuesta fue detenida por MAX_TOKENS.")
        
        return api_result_full

    def _call_gemini_api_batch(self, cleaned_texts_list: list) -> list[str]:
        if not cleaned_texts_list:
            return []
        
        batch_text_numbered = add_line_numbers(cleaned_texts_list)
        batch_size_info = f"{len(cleaned_texts_list)} líneas"
        
        prompt = self.config['BATCH_TRANSLATION_PROMPT_TEMPLATE'].format(
            target_language_name=self.config['TARGET_LANGUAGE_NAME'],
            batch_size_info=batch_size_info,
            batch_text=batch_text_numbered,
            series_title=self.config.get('SERIES_TITLE', 'Desconocido'),
            episode_title=self.config.get('EPISODE_TITLE', 'Desconocido')
        )
        
        max_retries = self.config['API_MAX_RETRIES']
        current_delay = self.config['API_RETRY_INITIAL_DELAY']

        for attempt in range(max_retries + 1):
            current_model = self.model_manager.get_current_model()
            if not current_model:
                logging.error("No hay modelos disponibles para la traducción.")
                raise APIResponseError("Todos los modelos agotados.")

            try:
                response = self.client.models.generate_content(
                    model=current_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        safety_settings=self._get_safety_settings(),
                        http_options=types.HttpOptions(timeout=self.config.get('API_BATCH_TIMEOUT', 300) * 1000)
                    ),
                )
                
                api_result_full = self._handle_api_response(response, is_batch=True)
                
                parsed_results_dict = parse_numbered_response(api_result_full, len(cleaned_texts_list))
                missing_indices = validate_response_indices(parsed_results_dict, len(cleaned_texts_list))
                
                if missing_indices:
                    logging.warning(f"API Batch Error Conteo! Faltan indices: {missing_indices}")
                    if attempt < max_retries:
                        logging.info(f"Reintentando en {current_delay}s...")
                        time.sleep(current_delay)
                        current_delay *= 2
                        continue
                    else:
                        raise LineCountMismatchError(len(cleaned_texts_list), len(parsed_results_dict), missing_indices)
                
                final_ordered_results = [parsed_results_dict[i] for i in range(1, len(cleaned_texts_list) + 1)]
                return final_ordered_results

            except ContentBlockedError:
                raise
            except Exception as e:
                if self._is_rate_limit_error(e):
                    logging.warning(f"Rate limit detectado en modelo '{current_model}'.")
                    self.model_manager.report_rate_limit(current_model)
                    
                    if self.model_manager.has_more_alternatives():
                        next_model = self.model_manager.switch_to_next_model()
                        logging.info(f"Cambiando automáticamente a modelo: {next_model}")
                        continue
                    else:
                        # MEJORA: Usar can_reset() para verificar si podemos resetear
                        if self.model_manager.can_reset() and attempt < max_retries:
                            logging.warning("No hay más modelos alternativos. Esperando cuota...")
                            time.sleep(self.config.get('RATE_LIMIT_WAIT_SECONDS', 60))
                            if not self.model_manager.reset_blocked_models():
                                raise APIResponseError("Límite de resets globales alcanzado.")
                            continue
                        else:
                            raise APIResponseError("Todos los modelos agotados y sin posibilidad de reset.")
                
                if self._is_retryable_error(e) and attempt < max_retries:
                    logging.warning(f"Error reintentable (intento {attempt+1}): {e}")
                    time.sleep(current_delay)
                    current_delay *= 2
                else:
                    logging.error(f"Error fatal en API Batch: {e}")
                    raise APIResponseError(f"Fallo en batch tras reintentos: {str(e)}")
        
        raise APIResponseError("Se agotaron los reintentos en batch.")

    def translate_single_gemini(self, original_text: str, cache: TranslationCache):
        if not original_text or original_text.isspace():
            return ""
        
        if self.config['ENABLE_TRANSLATION_CACHE'] and cache is not None and original_text in cache:
            return cache.get(original_text)
        
        cleaned_text, tags = extract_tags(original_text)
        if not cleaned_text.strip():
             return original_text
        
        prompt = self.config['SINGLE_TRANSLATION_PROMPT_TEMPLATE'].format(
            target_language_name=self.config['TARGET_LANGUAGE_NAME'],
            text=cleaned_text,
            series_title=self.config.get('SERIES_TITLE', 'Desconocido'),
            episode_title=self.config.get('EPISODE_TITLE', 'Desconocido')
        )
        
        max_retries = self.config['API_MAX_RETRIES']
        current_delay = self.config['API_RETRY_INITIAL_DELAY']

        for attempt in range(max_retries + 1):
            current_model = self.model_manager.get_current_model()
            if not current_model: return "[[ERROR_MODELOS_AGOTADOS]]"

            try:
                response = self.client.models.generate_content(
                    model=current_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        safety_settings=self._get_safety_settings(),
                        http_options=types.HttpOptions(timeout=self.config.get('API_SINGLE_TIMEOUT', 120) * 1000)
                    ),
                )
                
                api_result = self._handle_api_response(response, is_batch=False)
                translated_cleaned = api_result.strip()
                if translated_cleaned.startswith('"') and translated_cleaned.endswith('"'): translated_cleaned = translated_cleaned[1:-1]
                
                final_text = restore_tags(translated_cleaned, tags)
                if self.config['ENABLE_TRANSLATION_CACHE'] and cache is not None:
                    cache.set(original_text, final_text)
                return final_text

            except ContentBlockedError:
                return f"[[CONTENIDO_BLOQUEADO: {original_text[:20]}...]]"
            except Exception as e:
                if self._is_rate_limit_error(e):
                    logging.warning(f"Rate limit detectado en modelo '{current_model}' (Single).")
                    self.model_manager.report_rate_limit(current_model)
                    if self.model_manager.has_more_alternatives():
                        self.model_manager.switch_to_next_model()
                        continue
                    else:
                        if self.model_manager.can_reset() and attempt < max_retries:
                            logging.warning("Esperando cuota para traducción individual...")
                            time.sleep(self.config.get('RATE_LIMIT_WAIT_SECONDS', 60))
                            if self.model_manager.reset_blocked_models():
                                continue
                
                if self._is_retryable_error(e) and attempt < max_retries:
                    time.sleep(current_delay)
                    current_delay *= 2
                else:
                    logging.error(f"Error en single translation: {e}")
                    return f"[[ERROR_API_SINGLE: {str(e)[:50]}]]"
        
        return "[[ERROR_MAX_RETRIES_SINGLE]]"

    def translate_recursive_fallback(self, original_texts: list, cache: TranslationCache, current_level=0, max_level=3):
        indent = "  " * current_level
        logging.debug(f"{indent}Nivel {current_level}: Procesando {len(original_texts)} líneas.")
        
        final_results = [None] * len(original_texts)
        texts_to_process_indices = []
        cleaned_to_translate = []
        
        for i, text in enumerate(original_texts):
            if not text or text.isspace():
                final_results[i] = ""
            elif self.config['ENABLE_TRANSLATION_CACHE'] and cache is not None and text in cache:
                final_results[i] = cache.get(text)
            else:
                cleaned, tags = extract_tags(text)
                if not cleaned.strip():
                    final_results[i] = text
                else:
                    texts_to_process_indices.append(i)
                    cleaned_to_translate.append(cleaned)

        if not cleaned_to_translate:
            return final_results

        try:
            batch_size = max(1, self.config.get('BATCH_SIZE', 20))
            total = len(cleaned_to_translate)
            total_chunks = math.ceil(total / batch_size)

            all_batch_results = []
            for chunk_idx in range(total_chunks):
                chunk_start = chunk_idx * batch_size
                chunk_end = min(chunk_start + batch_size, total)
                chunk = cleaned_to_translate[chunk_start:chunk_end]

                logging.info(f"{indent}Traduciendo chunk {chunk_idx+1}/{total_chunks} "
                             f"({chunk_start+1}-{chunk_end}/{total} líneas)")

                chunk_results = self._call_gemini_api_batch(chunk)
                all_batch_results.extend(chunk_results)

                # Delay entre chunks para evitar rate limits
                if self.config['API_CALL_DELAY'] > 0 and chunk_idx + 1 < total_chunks:
                    time.sleep(self.config['API_CALL_DELAY'])

            batch_translated = all_batch_results
            for idx, trans_cleaned in enumerate(batch_translated):
                orig_idx = texts_to_process_indices[idx]
                _, tags = extract_tags(original_texts[orig_idx])
                final_trans = restore_tags(trans_cleaned, tags)
                final_results[orig_idx] = final_trans
                if self.config['ENABLE_TRANSLATION_CACHE'] and cache is not None:
                    cache.set(original_texts[orig_idx], final_trans)
            
            # Limpiar estado tras éxito
            self.model_manager.clear_on_success()
            return final_results

        except ContentBlockedError:
            logging.error(f"{indent}Lote completo bloqueado por seguridad.")
            for i in texts_to_process_indices:
                final_results[i] = "[[LOTE_BLOQUEADO_SEGURIDAD]]"
            return final_results

        except (APIResponseError, LineCountMismatchError) as e:
            logging.warning(f"{indent}Nivel {current_level}: Batch falló ({type(e).__name__}). Fallback...")
            if current_level >= max_level or len(original_texts) <= 1:
                logging.info(f"{indent}Fallback final a LxL...")
                for i in texts_to_process_indices:
                    final_results[i] = self.translate_single_gemini(original_texts[i], cache)
                    if self.config['API_CALL_DELAY'] > 0: time.sleep(self.config['API_CALL_DELAY'])
                return final_results
            else:
                mid = math.ceil(len(original_texts) / 2)
                first_half = original_texts[:mid]
                second_half = original_texts[mid:]
                
                res1 = self.translate_recursive_fallback(first_half, cache, current_level + 1, max_level)
                
                # MEJORA: Añadir delay entre bloques divididos para evitar rate limits
                if self.config['API_CALL_DELAY'] > 0:
                    time.sleep(self.config['API_CALL_DELAY'])
                
                res2 = self.translate_recursive_fallback(second_half, cache, current_level + 1, max_level)
                return res1 + res2
