"""
tests/test_updater_modes.py — El updater preserva/ejecuta permisos al aplicar.

Regresión del incidente post-2026.08.10: el zipball no trae bits Unix y el
entry script aterrizaba sin +x (Permission denied en Sonarr).
"""
import io
import json
import zipfile
from pathlib import Path

import pytest

from src import updater


class FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _zipball(entry_content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Generacion_Sub_AI-2026.08.99/Generacion_Sub_AI.py", entry_content)
        z.writestr("Generacion_Sub_AI-2026.08.99/src/constants.py", "X = 1\n")
    return buf.getvalue()


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Script dir con entry existente ejecutable + releases API simulada."""
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    entry = script_dir / "Generacion_Sub_AI.py"
    entry.write_text("# viejo\n", encoding="utf-8")
    entry.chmod(0o755)

    api = FakeResp(json.dumps({
        "tag_name": "v2026.08.99",
        "zipball_url": "https://example.com/zipball",
    }).encode())
    zip_bytes = _zipball("# nuevo\n")

    respuestas = [api, FakeResp(zip_bytes)]

    def fake_urlopen(req, timeout=10):
        return respuestas.pop(0)

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
    return {"script_dir": script_dir, "entry": entry}


def test_update_preserva_exec_bit_del_entry(entorno):
    updater.check_and_update("2026.08.9", entorno["script_dir"])

    entry = entorno["entry"]
    assert entry.read_text(encoding="utf-8") == "# nuevo\n"   # contenido actualizado
    assert entry.stat().st_mode & 0o111                       # sigue ejecutable
    # archivo anidado sin modo previo: default de copy2 (sin crash)
    assert (entorno["script_dir"] / "src" / "constants.py").exists()


def test_update_entry_sin_x_previo_queda_ejecutable(entorno):
    """Auto-reparación: aunque la instalación rota haya perdido +x, tras
    actualizar vuelve a 0755 (no se preserva el modo roto)."""
    entry = entorno["entry"]
    entry.chmod(0o644)                                        # estado roto
    updater.check_and_update("2026.08.9", entorno["script_dir"])
    assert entry.read_text(encoding="utf-8") == "# nuevo\n"
    assert entry.stat().st_mode & 0o111                       # reparado
