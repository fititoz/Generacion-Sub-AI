"""
tests/test_selectors.py — Fixes A-SEL S1 (und como fuente), A-REORDER S1
(es-419 IETF invisible) y B-TAG S1 (colisión __TAGn__).
"""
import dataclasses
import os
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from src.config_manager import load_config

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "config.ini.example"


def _cfg(**overrides):
    cfg, _ = load_config(EXAMPLE, capture_warnings=True)
    return dataclasses.replace(cfg, **overrides) if overrides else cfg


def _track(tid, lang, codec="S_TEXT/ASS", name=""):
    return NS(track_id=tid, track_type='subtitles', language=lang,
              codec_id=codec, track_name=name, default_track=False,
              forced_track=False)


# --- Fix #6: 'und' ya no es fuente -----------------------------------------

def _select(gsa, tracks, codecs=None, cfg=None):
    codecs = codecs or {t.track_id: t.codec_id for t in tracks}
    return gsa.select_subtitle_track(tracks, codecs, cfg or _cfg())


@pytest.mark.skipif(os.name == 'nt', reason="import del entry requiere binarios")
def test_und_only_no_devuelve_fuente():
    import Generacion_Sub_AI as gsa
    tracks = [_track(1, 'und'), _track(2, 'und')]
    assert _select(gsa, tracks) is None


@pytest.mark.skipif(os.name == 'nt', reason="import del entry requiere binarios")
def test_eng_gana_sobre_und():
    import Generacion_Sub_AI as gsa
    tracks = [_track(1, 'und'), _track(2, 'eng')]
    elegida = _select(gsa, tracks)
    assert elegida.track_id == 2


@pytest.mark.skipif(os.name == 'nt', reason="import del entry requiere binarios")
def test_preferred_source_lang_se_honra():
    import Generacion_Sub_AI as gsa
    tracks = [_track(1, 'eng'), _track(2, 'fre'), _track(3, 'ger')]
    elegida = _select(gsa, tracks)
    assert elegida.track_id == 2  # preferred_source_lang=fre


# --- Fix #7: reorder lee language_ietf --------------------------------------

MKVMERGE_STUB = """#!/bin/sh
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    *) if [ -n "$CAPTURE" ]; then printf '%s\\n' "$1" >> "$CAPTURE"; fi; shift ;;
  esac
done
[ -n "$out" ] && printf "MKV0" > "$out"
exit 0
"""


@pytest.fixture
def stub_mkvmerge(tmp_path):
    """mkvmerge falso: crea el -o y vuelca argv a un archivo de captura."""
    capture = tmp_path / "argv.txt"
    script = tmp_path / "fake-mkvmerge.sh"
    script.write_text(MKVMERGE_STUB.replace('$CAPTURE', str(capture)), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return {"exe": str(script), "capture": capture}


@pytest.mark.skipif(os.name == 'nt', reason="stub mkvmerge es shell POSIX")
def test_reorder_detecta_es419_por_ietf(tmp_path, stub_mkvmerge):
    from src.track_reorder import reorder_tracks

    mkv = tmp_path / "ep.mkv"
    mkv.write_bytes(b"\x00")  # contenido fake; el stub no lo lee
    cfg = _cfg(replace_original_mkv=False)

    def tr(tid, lang_legacy, ietf, name):
        props = {}
        if lang_legacy:
            props["language"] = lang_legacy
        if ietf:
            props["language_ietf"] = ietf
        props["track_name"] = name
        return {"id": tid, "type": "subtitles", "properties": props}

    mkv_info = {"tracks": [
        {"id": 0, "type": "video", "properties": {"language": "jpn"}},
        {"id": 1, "type": "audio", "properties": {"language": "jpn"}},
        tr(2, None, "es-419", ""),          # latino SOLO visible vía IETF
        tr(3, "spa", "", ""),               # genérico
        tr(4, "eng", "", ""),
    ]}
    ok = reorder_tracks(mkv, mkv_info, cfg,
                        {"mkvmerge": stub_mkvmerge["exe"]}, None)
    assert ok is True

    argv = stub_mkvmerge["capture"].read_text(encoding="utf-8").splitlines()
    # el stub vuelca un argumento por línea: --track-order va seguido de su valor
    i = argv.index("--track-order")
    order = argv[i + 1]
    # el sub es-419 (id 2) debe ir primero entre los subs
    assert order.split(",")[:3] == ["0:0", "0:1", "0:2"], order
    assert "2:yes" in argv and "3:no" in argv


@pytest.mark.skipif(os.name == 'nt', reason="stub mkvmerge es shell POSIX")
def test_reorder_no_pierde_generico_por_ietf_variante(tmp_path, stub_mkvmerge):
    """legacy spa + ietf es-ES: el tier genérico NO se pierde por el IETF raro."""
    from src.track_reorder import reorder_tracks

    mkv = tmp_path / "ep.mkv"
    mkv.write_bytes(b"\x00")
    cfg = _cfg(replace_original_mkv=False)

    def tr(tid, legacy, ietf, name):
        props = {}
        if legacy:
            props["language"] = legacy
        if ietf:
            props["language_ietf"] = ietf
        props["track_name"] = name
        return {"id": tid, "type": "subtitles", "properties": props}

    mkv_info = {"tracks": [
        {"id": 0, "type": "video", "properties": {"language": "jpn"}},
        {"id": 1, "type": "audio", "properties": {"language": "jpn"}},
        tr(2, "spa", "es-ES", ""),   # dual: legacy genérico + IETF variante España
        tr(3, "eng", "", ""),
    ]}
    ok = reorder_tracks(mkv, mkv_info, cfg,
                        {"mkvmerge": stub_mkvmerge["exe"]}, None)
    assert ok is True

    argv = stub_mkvmerge["capture"].read_text(encoding="utf-8").splitlines()
    i = argv.index("--default-track-flag")
    assert argv[i + 1] == "2:yes", argv   # el sub dual queda como default


# --- Fix #8: colisión de literales __TAGn__ ---------------------------------

def test_extract_reserva_indices_sin_colision():
    from src.tag_handler import extract_tags, restore_tags

    original = r'ya había __TAG0__ aquí {\i1}real\Nfin'
    cleaned, tags = extract_tags(original)

    # los placeholders nuevos NO pueden ser __TAG0__ (reservado por el literal)
    # el literal quedó escapado (invisible para modelo y parser);
    # el __TAG0__ restante es el placeholder LEGÍTIMO del salto \N
    assert "__TAGL0__" in cleaned

    restored = restore_tags(cleaned, tags)
    assert restored == original            # round-trip exacto pese al literal


def test_duplicado_deja_residual_visible(caplog):
    from src.tag_handler import extract_tags, restore_tags

    cleaned, tags = extract_tags(r'{\i1}hola')
    assert tags == [r"{\i1}"]
    duplicado = "a __TAG0__ b __TAG0__ c"   # el modelo duplicó el placeholder
    with caplog.at_level("WARNING"):
        out = restore_tags(duplicado, tags)
    assert out == r"a {\i1} b __TAG0__ c"  # primera restaurada, residual visible
    assert any("inesperados" in r.message.lower() or "Placeholders" in r.message
               for r in caplog.records)


def test_unknown_tag_id_pasa_a_traves():
    from src.tag_handler import extract_tags, restore_tags

    cleaned, tags = extract_tags(r'{\b1}x')
    out = restore_tags("__TAG9__ x", tags)
    assert out == "__TAG9__ x"             # id fuera de rango: sin tocar
