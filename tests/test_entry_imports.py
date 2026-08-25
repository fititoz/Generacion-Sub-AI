"""
tests/test_entry_imports.py — Vacuna contra NameErrors de imports cruzados.

La extracción a src/phases.py rompió producción con
`NameError: name 'phases' is not defined` porque py_compile no caza
NameErrors y ningún test ejecutaba process_file. Este archivo importa el
entry completo y verifica que cada símbolo cruzado exista, además de
ejecutar process_file lo suficiente para atravesar la primera fase.
"""
import importlib

import pytest

from src.config_manager import load_config


@pytest.fixture(scope="module")
def gsa():
    return importlib.import_module("Generacion_Sub_AI")


def test_simbolos_cruzados_presentes(gsa):
    esperados = [
        "phases",              # el que faltó en el incidente
        "load_config",
        "FileContext",
        "is_error_sentinel",
        "make_error",
        "embed_translation",
        "_remux_embed_chapters",
        "reorder_tracks",
    ]
    faltan = [s for s in esperados if not hasattr(gsa, s)]
    assert not faltan, f"imports cruzados ausentes en entry: {faltan}"


def test_process_file_atraviesa_primera_fase(gsa, tmp_path):
    """Con un MKV fake y sin herramientas, debe pasar por phases.probe y
    terminar de forma controlada (pymkv fallará -> retorno temprano)."""
    mkv = tmp_path / "fake.mkv"
    mkv.write_bytes(b"\x1a45dfa3garbage")   # firma EBML + basura
    ctx = {"mkv_path": mkv, "series_title": "S", "episode_title": "E",
           "season_number": 1, "chapters_override": None}
    cfg, _ = load_config(gsa.Path(__file__).resolve().parent.parent / "config.ini.example",
                         capture_warnings=True)
    # No debe lanzar NameError ni ninguna excepción no controlada:
    gsa.process_file(ctx, cfg, {}, translation_cache=None)
