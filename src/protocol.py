"""
protocol.py — Protocolo de traducción: dueño del vocabulario de cable.

Un solo módulo define los tres lenguajes que atraviesan la frontera LLM:

1. Gramática de numeración `[N]:` — número inicial, línea canónica y las
   formas toleradas al parsear (la tolerancia vive en line_numbering; los
   patrones canónicos viven aquí para que productor, parser y validador
   compartan la misma definición).
2. Sentinelas de error `[[KIND: mensaje]]` — producción, detección y kinds
   registrados. Un único predicado reemplaza a los tres criterios distintos
   que existían; texto legítimo como `[[OVA]]` ya NO se confunde con error.
3. Alfabeto de placeholders `__TAGn__` — prefijo/sufijo viven físicamente en
   constants.py (convención del repo); aquí viven su regex y el predicado.

Además publica REQUIRED_PROMPT_TOKENS: los tokens que todo template de
prompt debe contener para seguir de acuerdo con el parser. El test de
paridad lo exige sobre defaults y sobre config.ini.example.
"""
import re

from src.constants import PLACEHOLDER_PREFIX, PLACEHOLDER_SUFFIX


# ---------------------------------------------------------------------------
# 1. Gramática [N]:
# ---------------------------------------------------------------------------

NUMBERING_START = 1


def format_numbered_line(index: int, text: str) -> str:
    """Línea numerada canónica: `[N]: texto` (N empieza en NUMBERING_START)."""
    if index < NUMBERING_START:
        raise ValueError(f"índice {index} < NUMBERING_START ({NUMBERING_START})")
    return f"[{index}]: {text}"


# Patrón principal: [N]: texto.
# El marcador siguiente debe empezar en línea nueva: así "21:45" u otros
# números con ':' dentro del texto NO truncan el contenido (S1 de la auditoría).
PRIMARY_NUMBERED_RE = re.compile(r"\[(\d+)\]:\s*(.*?)(?=\n\s*\[?\d+\]?:|\Z)", re.DOTALL)

# Formas alternativas toleradas al parsear respuestas mal formateadas
ALT_NUMBERED_PATTERNS = (
    re.compile(r"\[(\d+)\]\s*:\s*(.*?)(?=\s*\[\d+\]|$)", re.DOTALL),      # [N] : texto
    re.compile(r"(\d+)\):\s*(.*?)(?=\s*\d+\)|$)", re.DOTALL),             # N): texto
    # N. texto — SOLO si parece enumerado real (no "50. Niente...")
    re.compile(r"^(\d+)\.\s+(.+?)(?=^\d+\.\s+|\Z)", re.MULTILINE | re.DOTALL),
    re.compile(r"^(\d+):\s*(.*?)(?=^\d+:|$)", re.MULTILINE | re.DOTALL),  # N: texto
)

# Artefacto de respuesta cruda: "N. texto" sin corchetes (rechazo temprano /
# flag del validador). Un solo patrón para ambos usos.
NUMBERED_ARTIFACT_RE = re.compile(r"^\d+\.\s+\w+", re.MULTILINE)


# ---------------------------------------------------------------------------
# 2. Sentinelas [[KIND: mensaje]]
# ---------------------------------------------------------------------------

ERROR_PREFIX = "[["

# Kinds producidos por el producto. Ampliar aquí al agregar uno nuevo.
REGISTERED_KINDS = frozenset({"ERROR_API_SINGLE", "ERROR_FATAL_TRADUCTOR"})

_SENTINEL_RE = re.compile(r"^\[\[([A-Z_][A-Z_0-9]*)(?:[ :].*)?\]\]$")


def make_error(kind: str, message: str = "") -> str:
    """Produce un sentinela registrado. Rechaza kinds no registrados."""
    if kind not in REGISTERED_KINDS:
        raise ValueError(f"kind de sentinela no registrado: {kind!r}")
    if message:
        return f"[[{kind}: {message}]]"
    return f"[[{kind}]]"


def error_sentinel_kind(text: str) -> str | None:
    """Kind del sentinela si el texto ES un error registrado; None si no."""
    match = _SENTINEL_RE.match((text or "").strip())
    if match and match.group(1) in REGISTERED_KINDS:
        return match.group(1)
    return None


def is_error_sentinel(text: str) -> bool:
    """True sii el texto es un sentinela [[KIND…]] con KIND registrado."""
    return error_sentinel_kind(text) is not None


# ---------------------------------------------------------------------------
# 3. Alfabeto __TAGn__
# ---------------------------------------------------------------------------

PLACEHOLDER_EXAMPLE = f"{PLACEHOLDER_PREFIX}0{PLACEHOLDER_SUFFIX}"
_PLACEHOLDER_RE = re.compile(re.escape(PLACEHOLDER_PREFIX) + r"\d+" + re.escape(PLACEHOLDER_SUFFIX))


def contains_placeholder(text) -> bool:
    """True si el texto contiene algún marcador __TAGn__ sin restaurar."""
    return isinstance(text, str) and bool(_PLACEHOLDER_RE.search(text))


# ---------------------------------------------------------------------------
# Tokens obligatorios del prompt batch
# ---------------------------------------------------------------------------

REQUIRED_PROMPT_TOKENS = (
    "[N]:",            # formato canónico enseñado
    "[1]",             # numeración 1-based explícita
    "nunca en [0]",    # blindaje anti 0-index
    "__TAG0__",        # alfabeto de placeholders presente
)
