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


def strip_source_echo(translated: str, source: str) -> str:
    """Elimina el eco 'original -> traducción' que algunos modelos imprimen en vez de solo la traducción."""
    t = (translated or "").strip()
    s = (source or "").strip()
    if not t or not s or "->" not in t:
        return translated
    probe = s[:40]
    if t.startswith(probe):
        _, _, tail = t.rpartition("->")
        tail = tail.strip()
        if tail:
            return tail
    return translated


class APIClient:
    """Cliente universal para APIs compatibles con OpenAI (base_url + api_key)."""

    def __init__(self, api_key: str, base_url: str, model: str, config: dict):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.config = config
        self.client = None
        # Debug: truncado configurable del volcado de respuestas crudas (0 = sin volcado)
        self._debug = bool(config.get('DEBUG_TRANSLATION', False))
        self._debug_max_chars = int(config.get('DEBUG_MAX_CHARS', 800))
        self._last_call_ts = 0.0
        self._configure_client()

    def _dbg(self, stage: str, message: str):
        if self._debug:
            logging.info("[DEBUG][%s] %s", stage, message)

    def _dbg_dump(self, stage: str, text: str, tail: bool = False):
        """Vuelca texto crudo truncado. tail=True muestra el final (útil para detectar corte)."""
        if not self._debug:
            return
        max_c = self._debug_max_chars
        if max_c <= 0 or len(text) <= max_c:
            logging.info("[DEBUG][%s] RAW (%d chars): %r", stage, len(text), text)
            return
        shown = text[-max_c:] if tail else text[:max_c]
        edge = "...(final)..." if tail else "..."
        logging.info("[DEBUG][%s] RAW (%d chars, mostrando %s%d): %s%s%s",
                     stage, len(text), "últimos " if tail else "", min(max_c, len(text)),
                     edge, repr(shown), edge)

    @property
    def current_model_name(self) -> str:
        return self.model

    def _configure_client(self):
        """Inicializa el cliente OpenAI y verifica la conexión con reintentos reales."""
        try:
            # max_retries=0: los reintentos los maneja _call_api con backoff real,
            # no el SDK con micro-backoffs de <1s que queman cuota bajo 429.
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.config.get('API_SINGLE_TIMEOUT', 120),
                max_retries=0,
            )
            # Test de conexión vía _call_api: hereda pacing + reintentos + espera de 429
            self._call_api([{"role": "user", "content": "test"}])
            logging.info("API conectada: %s (%s)", self.model, self.base_url)
        except Exception as e:
            logging.error("Falló la conexión inicial (tras reintentos): %s", e)
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
            "503", "502", "504", "500", "404",
            "service unavailable", "internal error", "internal server error",
            "temporary"
        ]
        return any(indicator in error_str for indicator in retryable_indicators)

    def _get_retry_after(self, exception) -> float | None:
        """Extrae el header Retry-After (segundos) si el proveedor lo envía."""
        try:
            response = getattr(exception, 'response', None)
            headers = getattr(response, 'headers', None) or {}
            value = headers.get('retry-after') or headers.get('Retry-After')
            return float(value) if value else None
        except (TypeError, ValueError):
            return None

    def _call_api(self, messages: list, max_retries: int = None, timeout: int = None) -> str:
        """Llama a la API con reintentos automáticos y backoff exponencial."""
        max_retries = max_retries or self.config.get('MAX_RETRIES', 3)
        base_delay = self.config.get('RETRY_BASE_DELAY', 2)
        
        for attempt in range(max_retries + 1):
            # Pacing: respetar api_call_delay entre llamadas para no saturar la API (evita 429)
            call_delay = float(self.config.get('API_CALL_DELAY', 0))
            if call_delay > 0:
                elapsed = time.time() - self._last_call_ts
                if elapsed < call_delay:
                    self._dbg("API", f"Pacing: esperando {call_delay - elapsed:.2f}s (api_call_delay={call_delay}s)")
                    time.sleep(call_delay - elapsed)
            self._last_call_ts = time.time()
            
            try:
                self._dbg("API", f"Intento {attempt + 1}/{max_retries + 1} (timeout={timeout or self.config.get('API_SINGLE_TIMEOUT', 120)}s)")
                if timeout:
                    # Crear cliente temporal con timeout personalizado
                    temp_client = OpenAI(
                        api_key=self.api_key,
                        base_url=self.base_url,
                        timeout=timeout,
                        max_retries=0,
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
                finish_reason = getattr(response.choices[0], 'finish_reason', '?')
                self._dbg("API", f"Intento {attempt + 1} OK. finish_reason={finish_reason}")
                if finish_reason == 'length':
                    logging.warning(
                        "Respuesta cortada por max_tokens (finish_reason=length). "
                        "Sube [LLM_SETTINGS] max_tokens o baja batch_size para evitar reintentos.")
                return content
                
            except Exception as e:
                if attempt < max_retries and self._is_retryable_error(e):
                    if self._is_rate_limit_error(e):
                        # 429: esperar de verdad — header Retry-After si existe, o RATE_LIMIT_WAIT_SECONDS
                        rl_wait = float(self.config.get('RATE_LIMIT_WAIT_SECONDS', 60))
                        retry_after = self._get_retry_after(e)
                        delay = min(retry_after, 120.0) if retry_after else rl_wait
                    else:
                        delay = base_delay * (2 ** attempt)
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

        self._dbg("BATCH", f"Enviando n={len(cleaned_texts_list)}. Primeras líneas: {cleaned_texts_list[:3]!r}")

        try:
            api_result_full = self._call_api(messages, timeout=self.config.get('API_BATCH_TIMEOUT', 300))

            # Etapa 2: respuesta cruda del modelo (cabeza; cola si el parseo falla)
            self._dbg_dump("BATCH", api_result_full)

            parsed_results_dict = parse_numbered_response(api_result_full, len(cleaned_texts_list))
            missing_indices = validate_response_indices(parsed_results_dict, len(cleaned_texts_list))

            if missing_indices:
                # Parseo parcial (típico con finish_reason=length): conservamos lo
                # parseado y devolvemos "" en las faltantes. El reintento por chunks
                # de translate_recursive_fallback recupera SOLO esas líneas.
                logging.warning(
                    "Parseo batch parcial: %d/%d recuperadas (%d faltantes). "
                    "Se reintentan solo las faltantes.",
                    len(parsed_results_dict), len(cleaned_texts_list), len(missing_indices))
                self._dbg("BATCH-PARSE", f"Parseo PARCIAL: {len(parsed_results_dict)}/{len(cleaned_texts_list)} "
                                         f"recuperadas. Última línea recibida: "
                                         f"{max(parsed_results_dict) if parsed_results_dict else 'N/A'}")
                self._dbg_dump("BATCH-PARSE", api_result_full, tail=True)
            else:
                self._dbg("BATCH-PARSE", f"Parseo OK: {len(parsed_results_dict)}/{len(cleaned_texts_list)}. Faltantes: []")

            # FIX off-by-one: parsed_results_dict usa claves 1..N (add_line_numbers
            # numera desde 1). range(0, N) dejaba results[0]="" y descartaba la línea N.
            results = [strip_source_echo(parsed_results_dict.get(i + 1, ""), cleaned_texts_list[i])
                       for i in range(len(cleaned_texts_list))]
            return results

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

            self._dbg("SINGLE", f"RAW: {translated_cleaned!r}")

            final_text = restore_tags(translated_cleaned, tags)
            if tags:
                self._dbg("RESTORE", f"tags={tags!r} -> final={final_text!r}")
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
            self._dbg("FALLBACK", f"L{current_level}: caso base individual para {len(cleaned_to_translate)} línea(s).")
            total_single = len(cleaned_to_translate)
            for pos, idx in enumerate(texts_to_process_indices, 1):
                result = self.translate_single(original_texts[idx], cache)
                final_results[idx] = result
                status = "OK" if not str(result).startswith("[[ERROR") else "ERROR"
                logging.info("%s[individual] %d/%d (línea %d): %s",
                             indent, pos, total_single, idx + 1, status)
        else:
            # Intentar batch POR CHUNKS de batch_size. Nunca mandar todo junto:
            # el modelo corta la salida por max_tokens y devuelve vacías desde cierto punto.
            batch_size = max(1, int(self.config.get('BATCH_SIZE', 50)))
            total_chunks = (len(cleaned_to_translate) + batch_size - 1) // batch_size
            try:
                batch_results = [None] * len(cleaned_to_translate)
                for chunk_no in range(total_chunks):
                    lo = chunk_no * batch_size
                    hi = min(lo + batch_size, len(cleaned_to_translate))
                    chunk_cleaned = cleaned_to_translate[lo:hi]
                    logging.info("%sBatch %d/%d: enviando %d líneas (batch_size=%d)...",
                                 indent, chunk_no + 1, total_chunks, len(chunk_cleaned), batch_size)
                    try:
                        chunk_out = self._call_api_batch(chunk_cleaned)
                    except Exception as e:
                        # Chunk entero falló: marcar sus líneas como vacías para que
                        # el reintento de abajo las divida. Los chunks previos OK se conservan.
                        self._dbg("FALLBACK", f"L{current_level}: chunk {chunk_no + 1}/{total_chunks} "
                                              f"falló ({e}); {len(chunk_cleaned)} líneas van a reintento.")
                        logging.warning("%sBatch %d/%d falló (%s); reintentando esas %d líneas divididas...",
                                        indent, chunk_no + 1, total_chunks, str(e)[:120], len(chunk_cleaned))
                        chunk_out = [""] * len(chunk_cleaned)
                    for j, r in enumerate(chunk_out):
                        batch_results[lo + j] = r
                all_ok = True
                empty_count = 0
                for idx, batch_result in zip(texts_to_process_indices, batch_results):
                    original = original_texts[idx]
                    cleaned, tags = extract_tags(original)
                    if batch_result and batch_result.strip():
                        final_results[idx] = restore_tags(batch_result, tags)
                        if tags:
                            self._dbg("RESTORE", f"L{idx+1} tags={tags!r} -> final={final_results[idx]!r}")
                        if self.config['ENABLE_TRANSLATION_CACHE'] and cache is not None:
                            cache.set(original, final_results[idx])
                    else:
                        all_ok = False
                        empty_count += 1
                        logging.warning("%sLínea %d: batch devolvió vacío.", indent, idx + 1)

                self._dbg("BATCH", f"L{current_level}: batch OK. {len(batch_results) - empty_count}/{len(batch_results)} líneas útiles, {empty_count} vacías.")

                # Fallback recursivo para las que fallaron
                if not all_ok:
                    failed_texts = [original_texts[i] for i, r in zip(texts_to_process_indices, batch_results) if not r or not r.strip()]
                    failed_indices = [i for i, r in zip(texts_to_process_indices, batch_results) if not r or not r.strip()]
                    if failed_texts:
                        logging.info("%sReintentando %d líneas fallidas en nivel %d...", indent, len(failed_texts), current_level + 1)
                        retry_results = self.translate_recursive_fallback(failed_texts, cache, current_level + 1, max_level)
                        for orig_idx, retry_result in zip(failed_indices, retry_results):
                            final_results[orig_idx] = retry_result

            except LineCountMismatchError as e:
                # Fallback recursivo dividiendo el batch
                mid = len(cleaned_to_translate) // 2
                self._dbg("FALLBACK", f"L{current_level}: batch falló ({e}). Dividiendo {len(cleaned_to_translate)} "
                                      f"→ [1..{mid}] + [{mid+1}..{len(cleaned_to_translate)}]")
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
                self._dbg("FALLBACK", f"L{current_level}: batch falló ({e}). Dividiendo {len(cleaned_to_translate)} "
                                      f"→ [1..{mid}] + [{mid+1}..{len(cleaned_to_translate)}]")
                left_texts = [original_texts[i] for i in texts_to_process_indices[:mid]]
                right_texts = [original_texts[i] for i in texts_to_process_indices[mid:]]
                
                left_results = self.translate_recursive_fallback(left_texts, cache, current_level + 1, max_level)
                right_results = self.translate_recursive_fallback(right_texts, cache, current_level + 1, max_level)
                
                for idx, result in zip(texts_to_process_indices[:mid], left_results):
                    final_results[idx] = result
                for idx, result in zip(texts_to_process_indices[mid:], right_results):
                    final_results[idx] = result

        return final_results