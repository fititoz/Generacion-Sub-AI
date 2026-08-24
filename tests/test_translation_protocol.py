"""
tests/test_translation_protocol.py — Contrato del Protocolo de traducción.

Matriz de centinelas, round-trip de numeración con casos adversariales del
catálogo B-NUM-BAD, y presencia de REQUIRED_PROMPT_TOKENS en default y example.
"""
import configparser
import sys
from pathlib import Path

import pytest

from src.config_manager import SPEC, load_config
from src.line_numbering import add_line_numbers, parse_numbered_response
from src.protocol import (
    NUMBERING_START,
    REQUIRED_PROMPT_TOKENS,
    contains_placeholder,
    error_sentinel_kind,
    format_numbered_line,
    is_error_sentinel,
    make_error,
)

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "config.ini.example"


# --- Sentinelas ----------------------------------------------------------

@pytest.mark.parametrize("kind,msg", [
    ("ERROR_API_SINGLE", "Fallo en llamada API: timeout"),
    ("ERROR_API_SINGLE", ""),
    ("ERROR_FATAL_TRADUCTOR", ""),
])
def test_make_error_y_deteccion(kind, msg):
    s = make_error(kind, msg)
    assert s.startswith("[[") and s.endswith("]]")
    assert is_error_sentinel(s)
    assert error_sentinel_kind(s) == kind


@pytest.mark.parametrize("texto", [
    "[[OVA]]",                      # texto legítimo de anime con corchetes dobles
    "[[ova]]",                      # kind en minúsculas no es centinela
    "[[ERROR_DESCONOCIDO]]",        # kind no registrado
    "[[ ERROR_API_SINGLE ]]",       # espacios internos: no es forma canónica
    "Hola [[ERROR_API_SINGLE]]",    # prefijo dentro de texto normal
    "", None,
])
def test_falsos_positivos_no_son_error(texto):
    assert not is_error_sentinel(texto)


def test_make_error_rechaza_kinds_no_registrados():
    with pytest.raises(ValueError):
        make_error("ERROR_INVENTADO", "x")


# --- Numeración [N]: ------------------------------------------------------

def test_format_numbered_line_empieza_en_1():
    assert NUMBERING_START == 1
    assert format_numbered_line(1, "Hola") == "[1]: Hola"
    with pytest.raises(ValueError):
        format_numbered_line(0, "prohibido")


def test_roundtrip_con_contenido_adversarial():
    """Casos del catálogo B-NUM-BAD que antes truncaban o desalineaban."""
    lineas = [
        "El reloj marcaba 21:45",
        "Dijo: ve al [3] túnel",
        "Episodio 7: el regreso",
        "__TAG0__ con formato",
    ]
    batch = add_line_numbers(lineas)
    parsed = parse_numbered_response(batch, len(lineas))
    for i, original in enumerate(lineas, start=NUMBERING_START):
        assert parsed[i] == original, f"línea {i}: {parsed[i]!r} != {original!r}"


def test_respuesta_libre_N_punto_se_rechaza_para_fallback():
    assert parse_numbered_response("50. Niente correcto.\n51. Va bene.", 2) == {}


# --- Tokens obligatorios del prompt ---------------------------------------

def _norm(tpl):
    return "\n".join(l.strip() for l in tpl.strip().splitlines())


def test_required_prompt_tokens_en_default_y_example():
    cfg, _ = load_config(EXAMPLE, capture_warnings=True)
    templates = {
        "default": _norm(SPEC["prompt_batch_template"][3]),
        "example": _norm(cfg.prompt_batch_template),
    }
    for origen, tpl in templates.items():
        faltan = [t for t in REQUIRED_PROMPT_TOKENS if t not in tpl]
        assert not faltan, f"{origen}: tokens obligatorios ausentes {faltan}"


def test_placeholders():
    assert contains_placeholder("__TAG0__ restaurado parcial")
    assert contains_placeholder("previo __TAG12__")
    assert not contains_placeholder("texto limpio")
    assert not contains_placeholder(None)
