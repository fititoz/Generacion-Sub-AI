"""
tests/test_phases.py — Unidades extraídas de process_file (src/phases.py).
"""
import json
from types import SimpleNamespace as NS

import pysubs2
import pytest

from src import phases
from src.protocol import is_error_sentinel


# --- probe_mkv_info ---------------------------------------------------------

def test_probe_parsea_json_y_codecs(monkeypatch):
    payload = {"tracks": [
        {"id": 0, "properties": {"codec_id": "V_MPEGH"}},
        {"id": 2, "properties": {"codec_id": "S_TEXT/ASS"}},
        {"id": 3, "properties": {}},
    ]}

    class R:
        returncode = 0
        stdout = json.dumps(payload)

    monkeypatch.setattr(phases.subprocess, "run", lambda *a, **k: R())
    info, codecs = phases.probe_mkv_info("x.mkv", {"mkvmerge": "/bin/true"})
    assert codecs == {0: "V_MPEGH", 2: "S_TEXT/ASS"}
    assert info["tracks"][1]["properties"]["codec_id"] == "S_TEXT/ASS"


def test_probe_sin_tool_devuelve_vacio(monkeypatch):
    calls = []
    monkeypatch.setattr(phases.subprocess, "run", lambda *a, **k: calls.append(1))
    info, codecs = phases.probe_mkv_info("x.mkv", {})
    assert info is None and codecs == {} and not calls


def test_probe_con_salida_invalida_devuelve_none(monkeypatch):
    class R:
        returncode = 0
        stdout = "{no-json"

    monkeypatch.setattr(phases.subprocess, "run", lambda *a, **k: R())
    info, codecs = phases.probe_mkv_info("x.mkv", {"mkvmerge": "/bin/true"})
    assert info is None and codecs == {}


# --- load_subtitles ---------------------------------------------------------

def _save(tmp_path, fmt):
    subs = pysubs2.Timebase() if False else pysubs2.SSAFile()
    ev = pysubs2.SSAEvent()
    ev.text = "hola"
    subs.events.append(ev)
    p = tmp_path / f"out.{fmt}"
    subs.save(str(p), format_=fmt)
    return p


@pytest.mark.parametrize("fmt", ["srt", "ass"])
def test_load_subtitles_detecta_formato(tmp_path, fmt):
    p = _save(tmp_path, fmt)
    subs, out_fmt = phases.load_subtitles(p)
    assert out_fmt == fmt
    assert len(subs.events) == 1


def test_load_subtitles_archivo_basura_falla(tmp_path):
    p = tmp_path / "basura.ass"
    p.write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(ValueError):
        phases.load_subtitles(p)


# --- collect / translate / apply -------------------------------------------

class FakeClient:
    def __init__(self, behavior):
        self.behavior = behavior

    def translate_recursive_fallback(self, originals, cache):
        if self.behavior == "crash":
            raise RuntimeError("API muerta")
        return [f"T:{o}" for o in originals]


class FakeSub:
    def __init__(self, text="", is_comment=False):
        self.text = text
        self.is_comment = is_comment


def test_collect_translatable_ignora_comentarios_y_vacias():
    subs = [FakeSub("a"), FakeSub("", is_comment=True),
            FakeSub("b", is_comment=True), FakeSub(""), FakeSub("c")]
    lines, idx_map, orig_idx = phases.collect_translatable(subs)
    assert lines == ["a", "c"]
    assert idx_map == {0: 0, 1: 4}
    assert orig_idx == [0, 4]


def test_translate_lines_envuelve_fallo_fatal_en_sentinela():
    fake = FakeClient("crash")
    out = phases.translate_lines(fake, cache=None, originals=["a", "b"])
    assert len(out) == 2
    assert all(is_error_sentinel(t) for t in out)


def test_translate_lines_caso_normal_pasa():
    fake = FakeClient("ok")
    out = phases.translate_lines(fake, cache=None, originals=["a"])
    assert out == ["T:a"]


def test_apply_results_ok_y_sentinela():
    subs = [FakeSub("orig1"), FakeSub("orig2")]
    idx_map = {0: 0, 1: 1}
    results = ["trad1", "[[ERROR_API_SINGLE: timeout]]"]
    stats = phases.apply_results(subs, idx_map, results)
    assert stats == {"ok": 1, "errors": 1}
    assert subs[0].text == "trad1"           # \n reemplazado no aplica aquí
    assert subs[1].text == "orig2"           # sentinela: evento intacto
