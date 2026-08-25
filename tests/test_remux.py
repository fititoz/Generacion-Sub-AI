"""
tests/test_remux.py — Contrato del módulo Remux con stub binario.

Cubre: matriz rc 0/1/2, validación ligera y profunda (-J) antes del
reemplazo destructivo, atomicidad del temporal y limpieza en fallo.
"""
import os
import stat
from pathlib import Path

import pytest

from src.config_manager import load_config
from src.remux import embed_chapters, embed_translation, reorder_and_save

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "config.ini.example"

MKVMERGE_STUB = """#!/bin/sh
out=""
if [ "$1" = "-J" ]; then
  printf '%s' '{"tracks":[{"type":"video","properties":{}}],"container":{"properties":{"duration":100.0}}}'
  exit ${RC:-0}
fi
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ -n "$out" ]; then
  if [ "$EMPTY" = "1" ]; then : > "$out"; else printf "MKV0" > "$out"; fi
fi
exit ${RC:-0}
"""


def _cfg(**overrides):
    cfg, _ = load_config(EXAMPLE, capture_warnings=True)
    return dataclasses_replace(cfg, **overrides)


def dataclasses_replace(cfg, **kw):
    import dataclasses
    return dataclasses.replace(cfg, **kw)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Source fake + stub mkvmerge configurable por variables de entorno."""
    src = tmp_path / "ep.mkv"
    src.write_bytes(b"ORIGINAL")
    stub = tmp_path / "fake-mkvmerge.sh"
    stub.write_text(MKVMERGE_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    tools = {"mkvmerge": str(stub)}
    return {"src": src, "tools": tools, "tmp_path": tmp_path,
            "setenv": monkeypatch.setenv}


def _sub(tmp_path):
    p = tmp_path / "sub.es-419.ass"
    p.write_text("[Script Info]\n", encoding="utf-8")
    return p


# --- rc=0 ------------------------------------------------------------------

def test_embed_translation_rc0_no_destructivo(entorno):
    cfg = _cfg(replace_original_mkv=False)
    sub = _sub(entorno["tmp_path"])
    r = embed_translation(entorno["src"], sub, cfg, entorno["tools"])
    assert r.ok and r.output is not None
    assert r.output.name == "ep.traducido.mkv" and r.output.exists()
    assert entorno["src"].read_bytes() == b"ORIGINAL"   # original intacto
    assert not list(entorno["tmp_path"].glob("*.tmp.mkv"))


def test_embed_chapters_rc0_destructivo_pasa_validacion_profunda(entorno):
    cfg = _cfg(replace_original_mkv=True)
    chap = entorno["tmp_path"] / "chapters.ogm"
    chap.write_text("CHAPTER01=00:00:00.000\n", encoding="utf-8")
    r = embed_chapters(entorno["src"], chap, cfg, entorno["tools"])
    assert r.ok
    assert entorno["src"].read_bytes() == b"MKV0"       # reemplazo atómico aplicado


# --- rc=1: warnings son éxito ----------------------------------------------

def test_rc1_es_exito_con_warning(entorno, monkeypatch):
    monkeypatch.setenv("RC", "1")
    cfg = _cfg(replace_original_mkv=False)
    r = embed_translation(entorno["src"], _sub(entorno["tmp_path"]), cfg,
                          entorno["tools"])
    assert r.ok
    assert any("rc=1" in w for w in r.warnings)


# --- rc>=2: fallo limpio -----------------------------------------------------

def test_rc2_falla_y_preserva_original(entorno, monkeypatch):
    monkeypatch.setenv("RC", "2")
    cfg = _cfg(replace_original_mkv=False)
    r = embed_translation(entorno["src"], _sub(entorno["tmp_path"]), cfg,
                          entorno["tools"])
    assert not r.ok
    assert entorno["src"].read_bytes() == b"ORIGINAL"
    assert not list(entorno["tmp_path"].glob("*.tmp.mkv"))


# --- Validación pre-reemplazo (G4) ------------------------------------------

def test_salida_vacia_no_reemplaza_original(entorno, monkeypatch):
    monkeypatch.setenv("EMPTY", "1")     # muxing "exitoso" pero sin contenido
    cfg = _cfg(replace_original_mkv=True)  # destructivo
    r = embed_translation(entorno["src"], _sub(entorno["tmp_path"]), cfg,
                          entorno["tools"])
    assert not r.ok
    assert entorno["src"].read_bytes() == b"ORIGINAL"   # original intacto
    assert not list(entorno["tmp_path"].glob("*.tmp.mkv"))  # temporal limpiado


# --- reorder_and_save delegación ---------------------------------------------

@pytest.mark.skipif(os.name == 'nt', reason="stub POSIX")
def test_reorder_wrapper_delega(entorno):
    cfg = _cfg(replace_original_mkv=False)
    r = reorder_and_save(entorno["src"], "0:0,0:1", ["--default-track-flag", "0:no"],
                         cfg, entorno["tools"])
    assert r.ok
    assert r.output.name == "ep.reordered.mkv"
