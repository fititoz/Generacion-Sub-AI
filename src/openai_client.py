"""
openai_client.py — OpenAI-compatible API client for subtitle translation.

Handles API communication, batch/single translation, recursive fallback
on partial failures, rate limit detection, and automatic retry with backoff.
Compatible with any OpenAI-format API endpoint (OpenRouter, Together, Groq, Ollama, etc.).
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


class OpenAIClient:
    """Cliente para APIs compatibles con OpenAI (base_url + api_key)."""

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
                messages=[{"role": "user", "content": "Test"}],
                max_tokens=5,
            )

            if not test_response.choices:
                logging.warning("Modelo '%s' no devolvió respuesta en test.", self.model)
                raise SystemExit(f"Modelo '{self.model}' no devolvió respuesta en test.")

            logging.info("Conexión API OK con modelo '%s'.", self.model)

        except SystemExit:
            raise
        except Exception as e:
            logging.exception("Error crítico en configuración API:")
            raise SystemExit(f"Fallo conexión API: {e}")

    def _is_rate_limit_error(self, exception) -> bool:
        """Detecta específicamente errores 429 / Rate Limit."""
        if isinstance(exception, RateLimitError):
            return True
        error_str = str(exception).lower()
        return '429' in error_str or 'rate' in error_str or 'too many requests' in error_str

    def _is_retryable_error(self, exception) -> bool:
        """Determina si el error es recuperable mediante reintento."""
        if isinstance(exception, (RateLimitError, APITimeoutError, OpenAIConnectionError)):
            return True
        error_str = str(exception).lower()
        retryable_patterns = ['retry', 'internal', 'unavailable', 'deadline', 'timeout', '503', '500', '429']
        return any(pattern in error_str for pattern in retryable_patterns)

    def _call_api(self, messages: list, max_retries: int = None, timeout: int = None) -> str:
        """Realiza una llamada a la API con reintentos y backoff exponencial."""
        if max_retries is None:
            max_retries = self.config.get('API_MAX_RETRIES', 3)
        if timeout is None:
            timeout = self.config.get('API_SINGLE_TIMEOUT', 120)

        current_delay = self.config.get('API_RETRY_INITIAL_DELAY', 20)

        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.3,
                    timeout=timeout,
                )

                if not response.choices:
                    logging.warning("API devolvió respuesta vacía.")
                    raise APIResponseError("Respuesta vacía de la API.")

                result = response.choices[0].message.content
                if result is None:
                    logging.warning("API devolvió contenido None.")
                    raise APIResponseError("Contenido None en respuesta.")

                return result.strip()

            except Exception as e:
                if self._is_rate_limit_error(e):
                    logging.warning(f"Rate limit detectado (intento {attempt+1}/{max_retries+1}).")
                    if attempt < max_retries:
                        wait_time = self.config.get('RATE_LIMIT_WAIT_SECONDS', 60)
                        logging.info(f"Esperando {wait_time}s antes de reintentar...")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise APIResponseError("Rate limit persistente tras reintentos.")
                if self._is_retryable_error(e) and attempt < max_retries:
                    logging.warning(f"Error reintentable (intento {attempt+1}): {e}")
                    time.sleep(current_delay)
                    current_delay *= 2
                else:
                    logging.error(f"Error fatal en API: {e}")
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
                logging.warning(f"API Batch Error Conteo! Faltan indices: {missing_indices}")
                raise LineCountMismatchError(len(cleaned_texts_list), len(parsed_results_dict), missing_indices)

            final_ordered_results = [parsed_results_dict[i] for i in range(1, len(cleaned_texts_list) + 1)]
            return final_ordered_results

        except LineCountMismatchError:
            raise
        except Exception as e:
            logging.error(f"Error en batch API: {e}")
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
            logging.error(f"Error en traducción individual: {e}")
            return f"[[ERROR_API_SINGLE: {str(e)[:50]}]]"

    def translate_recursive_fallback(self, original_texts: list, cache: TranslationCache, current_level=0, max_level=3):
        """Traduce con fallback recursivo: batch → dividir → individual."""
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

                chunk_results = self._call_api_batch(chunk)
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
                    final_results[i] = self.translate_single(original_texts[i], cache)
                    if self.config['API_CALL_DELAY'] > 0:
                        time.sleep(self.config['API_CALL_DELAY'])
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
