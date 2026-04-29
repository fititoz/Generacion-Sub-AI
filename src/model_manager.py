"""
model_manager.py — Gemini model rotation and rate limit management.

Maintains an ordered list of preferred models, tracks rate-limited models,
persists blocked state to disk for cross-invocation awareness, and provides
automatic failover with configurable global reset limits.
"""
import logging
import json
import time
from pathlib import Path

class ModelManager:
    STATE_FILE = Path(__file__).parent.parent / "cache" / "model_state.json"
    STATE_EXPIRY_SECONDS = 300  # 5 minutos

    def __init__(self, preferred_models: list, available_api_models: list | None = None):
        """
        Inicializa el gestor con una lista de modelos ordenados por prioridad.
        Si available_api_models se proporciona, filtrará los modelos preferidos
        para incluir solo los que existen en la API.
        """
        if available_api_models:
            # Normalizar nombres de modelos de la API
            api_model_names = {m.replace("models/", "") for m in available_api_models}
            api_model_names.update(available_api_models)  # Incluir también con prefijo
            
            validated_models = []
            for model in preferred_models:
                if model in api_model_names or f"models/{model}" in api_model_names:
                    validated_models.append(model)
                else:
                    logging.warning(f"Modelo preferido '{model}' no está disponible en la API. Ignorando.")
            
            self.preferred_models = validated_models
        else:
            self.preferred_models = preferred_models
        
        self.available_models = self.preferred_models.copy()
        self.current_model_index = 0
        self.blocked_models = set()
        self.global_reset_count = 0
        self.max_global_resets = 3
        
        if not self.available_models:
            logging.error("ModelManager inicializado sin modelos válidos.")
        
        # Cargar estado previo si existe
        self._load_state()

    def _load_state(self):
        """Carga el estado de modelos bloqueados desde el archivo."""
        try:
            if self.STATE_FILE.exists():
                with open(self.STATE_FILE, 'r') as f:
                    state = json.load(f)
                    
                # Verificar si el estado ha expirado
                saved_time = state.get('timestamp', 0)
                if time.time() - saved_time < self.STATE_EXPIRY_SECONDS:
                    self.blocked_models = set(state.get('blocked_models', []))
                    self.global_reset_count = state.get('global_reset_count', 0)
                    if self.blocked_models:
                        logging.info(f"Estado previo cargado: {len(self.blocked_models)} modelos bloqueados.")
                else:
                    logging.debug("Estado previo expirado, ignorando.")
                    self._clear_state()
        except Exception as e:
            logging.debug(f"No se pudo cargar estado previo: {e}")

    def _save_state(self):
        """Guarda el estado actual de modelos bloqueados."""
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.STATE_FILE, 'w') as f:
                json.dump({
                    'timestamp': time.time(),
                    'blocked_models': list(self.blocked_models),
                    'global_reset_count': self.global_reset_count
                }, f)
        except Exception as e:
            logging.debug(f"No se pudo guardar estado: {e}")

    def _clear_state(self):
        """Elimina el archivo de estado."""
        try:
            if self.STATE_FILE.exists():
                self.STATE_FILE.unlink()
        except Exception:
            pass

    def get_current_model(self) -> str | None:
        """Retorna el modelo actual que se debe usar."""
        if not self.available_models:
            return None
        
        while self.current_model_index < len(self.available_models):
            model = self.available_models[self.current_model_index]
            if model not in self.blocked_models:
                return model
            self.current_model_index += 1
            
        return None

    def switch_to_next_model(self) -> str | None:
        """Cambia al siguiente modelo disponible."""
        self.current_model_index += 1
        return self.get_current_model()

    def report_rate_limit(self, model_name: str):
        """Marca un modelo como bloqueado por rate limit."""
        logging.warning(f"Modelo '{model_name}' reportado con Rate Limit.")
        self.blocked_models.add(model_name)
        self._save_state()

    def reset_blocked_models(self) -> bool:
        """
        Limpia la lista de modelos bloqueados y reinicia el índice.
        Retorna False si se excedió el límite de resets globales.
        """
        self.global_reset_count += 1
        
        if self.global_reset_count > self.max_global_resets:
            logging.error(f"Se excedió el límite de {self.max_global_resets} resets globales. Abortando.")
            return False
        
        if self.blocked_models:
            logging.info(f"Reset global #{self.global_reset_count}: Reiniciando modelos bloqueados: {list(self.blocked_models)}")
            self.blocked_models.clear()
        self.current_model_index = 0
        self._save_state()
        return True

    def has_more_alternatives(self) -> bool:
        """Verifica si quedan modelos por probar."""
        temp_index = self.current_model_index + 1
        while temp_index < len(self.available_models):
            if self.available_models[temp_index] not in self.blocked_models:
                return True
            temp_index += 1
        return False

    def can_reset(self) -> bool:
        """Retorna True si aún quedan resets globales disponibles."""
        return self.global_reset_count < self.max_global_resets

    def get_all_active_models(self) -> list:
        """Retorna la lista de todos los modelos configurados."""
        return self.available_models

    def clear_on_success(self):
        """Limpia el estado persistido tras una ejecución exitosa."""
        self.global_reset_count = 0
        self.blocked_models.clear()
        self._clear_state()
