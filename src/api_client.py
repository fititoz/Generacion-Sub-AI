"""
api_client.py — Universal LLM API client for subtitle translation.

Handles API communication, batch/single translation, recursive fallback
on partial failures, rate limit detection, and automatic retry with backoff.
Compatible with any OpenAI-format API endpoint (OpenRouter, Together, Groq, Ollama, vLLM, OpenAI, etc.).
"""
import logging
import time
import math
from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError as OpenAIConnectionError
from src.tag_handler import extract_tags, restore_tags
from src.cache_manager import TranslationCache
from src.exceptions import (
    APIResponseError, LineCountMismatchError,
    ContentBlockedError, TranslationTimeoutError
)
from src.line_numbering import add_line_numbers, parse_numbered_response, validate_response_indices


class APIClient:
    """Cliente universal para APIs compatibles con OpenAI (base_url + api_key)."""

    def __init__(self, api_key: str, base_url: str, model: str, config: dict):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.config = config
        self.client = None
        self._configure_client()

    @property
    def current_model_name(self) -> str:
        return self.model

    def _configure_client(self):
        """Inicializa el cliente OpenAI y verifica la conexión."""
        try:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.config.get('API_SINGLE_TIMEOUT', 120),
            )
            logging.info("Probando conexión con modelo '%s'...", self.model)

            # Test de conexión
            test_response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5
            )
            logging.info("Conexión exitosa. Modelo: %s", self.model)
        except Exception as e:
            logging.error("Falló la conexión inicial: %s", e)
            raise

    def _is_rate_limit_error(self, exception) -> bool:
        """Detecta si el error es de rate limit (códigos 429, headers, etc.)."""
        if isinstance(exception, RateLimitError):
            return True
        error_str = str(exception).lower()
        rate_limit_indicators = [
            "rate limit", "429", "too many requests",
            "quota exceeded", "rate_limit_exceeded",
            "throttle", "rpm limit", "tpm limit"
        ]
        return any(indicator in error_str for indicator in rate_limit_indicators)

    def _is_retryable_error(self, exception) -> bool:
        """Determina si un error es reintentable."""
        if self._is_rate_limit_error(exception):
            return True
        if isinstance(exception, (APITimeoutError, OpenAIConnectionError)):
            return True
        error_str = str(exception).lower()
        retryable_indicators = [
            "timeout", "connection", "network",
            "503", "502", "504", "service unavailable",
            "internal error", "temporary"
        ]
        return any(indicator in error_str for indicator in retryable_indicators)

    def _call_api(self, messages: list, max_retries: int = None, timeout: int = None) -> str:
        """Llama a la API con reintentos automáticos y backoff exponencial."""
        max_retries = max_retries or self.config.get('MAX_RETRIES', 3)
        base_delay = self.config.get('RETRY_BASE_DELAY', 2)
        
        for attempt in range(max_retries + 1):
            try:
                if timeout:
                    # Crear cliente temporal con timeout personalizado
                    temp_client = OpenAI(
                        api_key=self.api_key,
                        base_url=self.base_url,
                        timeout=timeout
                    )
                    response = temp_client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=self.config.get('TEMPERATURE', 0.3),
                        max_tokens=self.config.get('MAX_TOKENS', 2000),
                    )
                else:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=self.config.get('TEMPERATURE', 0.3),
                        max_tokens=self.config.get('MAX_TOKENS', 2000),
                    )
                
                content = response.choices[0].message.content
                if content is None:
                    raise APIResponseError("Respuesta vacía de la API")
                return content
                
            except Exception as e:
                if attempt < max_retries and self._is_retryable_error(e):
                    delay = base_delay * (2 ** attempt)
                    if self._is_rate_limit_error(e):
                        delay *= 2  # Delay extra para rate limits
                    logging.warning("Intento %d/%d falló: %s. Reintentando en %.1fs...", 
                                  attempt + 1, max_retries + 1, e, delay)
                    time.sleep(delay)
                else:
                    logging.error("Error en llamada API: %s", e)
                    raise APIResponseError(f"Fallo en llamada API: {str(e)}")

        raise APIResponseError("Se agotaron los reintentos.")

    def _call_api_batch(self, cleaned_texts_list: list) -> list[str]:
        """Traduce un lote de líneas usando la API."""
        if not cleaned_texts_list:
            return []

        batch_text_numbered = add_line_numbers(cleaned_texts_list)
        batch_size_info = f"{len(cleaned_texts_list)} líneas"

        system_prompt = (
            "Eres un traductor experto de subtítulos especializado en anime. "
            "Responde SOLO con las traducciones numeradas en el formato [N]: texto, sin explicaciones."
        )
        user_prompt = self.config['BATCH_TRANSLATION_PROMPT_TEMPLATE'].format(
            target_language_name=self.config['TARGET_LANGUAGE_NAME'],
            batch_size_info=batch_size_info,
            batch_text=batch_text_numbered,
            series_title=self.config.get('SERIES_TITLE', 'Desconocido'),
            episode_title=self.config.get('EPISODE_TITLE', 'Desconocido')
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            api_result_full = self._call_api(messages, timeout=self.config.get('API_BATCH_TIMEOUT', 300))

            parsed_results_dict = parse_numbered_response(api_result_full, len(cleaned_texts_list))
            missing_indices = validate_response_indices(parsed_results_dict, len(cleaned_texts_list))

            if missing_indices:
                logging.warning("API Batch Error Conteo! Faltan indices: %s", missing_indices)
                raise LineCountMismatchError(f"Faltan índices en respuesta batch: {missing_indices}")

            results = [parsed_results_dict.get(i, "") for i in range(len(cleaned_texts_list))]
            return results

        except LineCountMismatchError:
            raise
        except Exception as e:
            logging.error("Error en batch API: %s", e)
            raise APIResponseError(f"Fallo en batch: {str(e)}")

    def translate_single(self, original_text: str, cache: TranslationCache):
        """Traduce una sola línea con fallback a caché."""
        if not original_text or original_text.isspace():
            return ""

        if self.config['ENABLE_TRANSLATION_CACHE'] and cache is not None and original_text in cache:
            return cache.get(original_text)

        cleaned_text, tags = extract_tags(original_text)
        if not cleaned_text.strip():
            return original_text

        system_prompt = (
            "Eres un traductor experto de subtítulos especializado en anime. "
            "Responde SOLO con la traducción, sin explicaciones ni comillas adicionales."
        )
        user_prompt = self.config['SINGLE_TRANSLATION_PROMPT_TEMPLATE'].format(
            target_language_name=self.config['TARGET_LANGUAGE_NAME'],
            text=cleaned_text,
            series_title=self.config.get('SERIES_TITLE', 'Desconocido'),
            episode_title=self.config.get('EPISODE_TITLE', 'Desconocido')
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            api_result = self._call_api(messages)
            translated_cleaned = api_result.strip()
            if translated_cleaned.startswith('"') and translated_cleaned.endswith('"'):
                translated_cleaned = translated_cleaned[1:-1]

            final_text = restore_tags(translated_cleaned, tags)
            if self.config['ENABLE_TRANSLATION_CACHE'] and cache is not None:
                cache.set(original_text, final_text)
            return final_text

        except Exception as e:
            logging.error("Error en traducción individual: %s", e)
            return f"[[ERROR_API_SINGLE: {str(e)[:50]}]]"

    def translate_recursive_fallback(self, original_texts: list, cache: TranslationCache, current_level=0, max_level=3):
        """Traduce con fallback recursivo: batch → dividir → individual."""
        indent = "  " * current_level
        logging.debug("%sNivel %d: Procesando %d líneas.", indent, current_level, len(original_texts))

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

        # Intentar batch primero
        if len(cleaned_to_translate) == 1 or current_level >= max_level:
            # Caso base: traducir individualmente
            for idx, cleaned_text in zip(texts_to_process_indices, cleaned_to_translate):
                original = original_texts[idx]
                result = self.translate_single(original, cache)
                final_results[idx] = result
        else:
            # Intentar batch
            try:
                batch_results = self._call_api_batch(cleaned_to_translate)
                all_ok = True
                for idx, batch_result in zip(texts_to_process_indices, batch_results):
                    original = original_texts[idx]
                    cleaned, tags = extract_tags(original)
                    if batch_result and batch_result.strip():
                        final_results[idx] = restore_tags(batch_result, tags)
                        if self.config['ENABLE_TRANSLATION_CACHE'] and cache is not None:
                            cache.set(original, final_results[idx])
                    else:
                        all_ok = False
                        logging.warning("%sLínea %d: batch devolvió vacío.", indent, idx + 1)

                # Fallback recursivo para las que fallaron
                if not all_ok:
                    failed_texts = [original_texts[i] for i, r in zip(texts_to_process_indices, batch_results) if not r or not r.strip()]
                    failed_indices = [i for i, r in zip(texts_to_process_indices, batch_results) if not r or not r.strip()]
                    if failed_texts:
                        logging.info("%sReintentando %d líneas fallidas en nivel %d...", indent, len(failed_texts), current_level + 1)
                        retry_results = self.translate_recursive_fallback(failed_texts, cache, current_level + 1, max_level)
                        for orig_idx, retry_result in zip(failed_indices, retry_results):
                            final_results[orig_idx] = retry_result

            except LineCountMismatchError:
                # Fallback recursivo dividiendo el batch
                mid = len(cleaned_to_translate) // 2
                left_texts = [original_texts[i] for i in texts_to_process_indices[:mid]]
                right_texts = [original_texts[i] for i in texts_to_process_indices[mid:]]
                
                left_results = self.translate_recursive_fallback(left_texts, cache, current_level + 1, max_level)
                right_results = self.translate_recursive_fallback(right_texts, cache, current_level + 1, max_level)
                
                for idx, result in zip(texts_to_process_indices[:mid], left_results):
                    final_results[idx] = result
                for idx, result in zip(texts_to_process_indices[mid:], right_results):
                    final_results[idx] = result
                    
            except Exception as e:
                logging.warning("%sBatch falló (%s), dividiendo...", indent, e)
                mid = len(cleaned_to_translate) // 2
                left_texts = [original_texts[i] for i in texts_to_process_indices[:mid]]
                right_texts = [original_texts[i] for i in texts_to_process_indices[mid:]]
                
                left_results = self.translate_recursive_fallback(left_texts, cache, current_level + 1, max_level)
                right_results = self.translate_recursive_fallback(right_texts, cache, current_level + 1, max_level)
                
                for idx, result in zip(texts_to_process_indices[:mid], left_results):
                    final_results[idx] = result
                for idx, result in zip(texts_to_process_indices[mid:], right_results):
                    final_results[idx] = result

        return final_results