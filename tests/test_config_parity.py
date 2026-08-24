"""
tests/test_config_parity.py — Paridad de la Configuración efectiva.

Cruza los tres mundos que antes vivían separados (y producían claves muertas):
  1. config.ini.example documenta
  2. SPEC (src/config_manager.py) define y coacciona
  3. consumidores usan atributos tipados (sin claves sueltas)

Ejecutar: python3 -m pytest tests/test_config_parity.py -q
"""
import configparser
import dataclasses
from pathlib import Path

import pytest

from src.config_manager import SPEC, Config, FileContext, load_config

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "config.ini.example"


def _example_keys():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(EXAMPLE, encoding="utf-8")
    return {(s, k.lower()) for s in parser.sections() for k in parser[s]}


def test_toda_clave_del_example_existe_en_SPEC():
    spec_keys = {(section, ini_key) for (_a, (section, ini_key, _c, _d)) in SPEC.items()}
    faltan_en_spec = _example_keys() - spec_keys
    assert not faltan_en_spec, f"documentadas pero sin dueño: {sorted(faltan_en_spec)}"


def test_toda_clave_de_SPEC_esta_documentada():
    spec_keys = {(section, ini_key) for (_a, (section, ini_key, _c, _d)) in SPEC.items()}
    no_documentadas = spec_keys - _example_keys()
    assert not no_documentadas, f"definidas pero sin documentar: {sorted(no_documentadas)}"


def test_no_resucitan_claves_muertas():
    muertas = {
        "api_max_retries", "api_retry_initial_delay",
        "rate_limit_max_global_retries", "add_subs_to_mkv",
    }
    presentes = {ini_key for (_s, ini_key) in _example_keys()} & muertas
    assert not presentes, f"claves eliminadas siguen en el example: {presentes}"


def test_config_es_congelada():
    assert dataclasses.fields(Config)
    cfg, _w = load_config(EXAMPLE, capture_warnings=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.batch_size = 1


def test_filecontext_es_congelada():
    fc = FileContext(
        mkv_path=Path("x.mkv"), series_title="S", episode_title="E",
        season_number=1, chapters_enabled=True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        fc.series_title = "otra"


def test_coerciones_tipadas(tmp_path):
    ini = tmp_path / "config.ini"
    # El example termina dentro de [DEBUG]: la clave inventada cae ahí mismo
    ini.write_text(
        EXAMPLE.read_text(encoding="utf-8") + "clave_inventada = 1\n",
        encoding="utf-8",
    )
    cfg, avisos = load_config(ini, capture_warnings=True)
    # tipos
    assert isinstance(cfg.batch_size, int) and cfg.batch_size == 50
    assert isinstance(cfg.api_call_delay, float)
    assert isinstance(cfg.debug_translation, bool) and cfg.debug_translation is False
    assert isinstance(cfg.enable_translation_cache, bool)
    assert cfg.target_language_codes[0] == "es-419"
    assert "latino" in cfg.latino_keywords
    assert cfg.theme_portion == pytest.approx(0.9)
    assert cfg.output_action in ("remux", "save_separate_sub")
    # derivados
    assert cfg.primary_target_code == "es-419"
    assert "spa" in cfg.target_language_codes_set
    # aviso por clave desconocida
    assert any("clave_inventada" in a for a in avisos), avisos


def test_valor_invalido_cae_al_default_con_aviso(tmp_path):
    ini = tmp_path / "config.ini"
    base = EXAMPLE.read_text(encoding="utf-8").replace(
        "batch_size = 50", "batch_size = cincuenta"
    )
    ini.write_text(base, encoding="utf-8")
    cfg, avisos = load_config(ini, capture_warnings=True)
    assert cfg.batch_size == 50
    assert any("batch_size" in a for a in avisos), avisos


def _norm(tpl: str) -> str:
    """Equivalencia efectiva: la indentación por línea es artefacto del INI."""
    return "\n".join(line.strip() for line in tpl.strip().splitlines())


def test_default_y_example_son_el_mismo_prompt():
    import configparser

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(EXAMPLE, encoding="utf-8")
    for attr, ini_key in (
        ("prompt_batch_template", "batch_template"),
        ("prompt_single_template", "single_template"),
    ):
        default_raw = SPEC[attr][3]
        documented = parser.get("PROMPTS", ini_key).strip()
        assert _norm(default_raw) == _norm(documented), f"drift {attr}"


def test_ruta_inexistente_genera_aviso(tmp_path):
    """El caso anime_path=C:\\... en un host Linux debe ser visible, no silencioso."""
    ini = tmp_path / "config.ini"
    base = EXAMPLE.read_text(encoding="utf-8").replace(
        "anime_path = /home/biblioteca/Anime", "anime_path = C:\\Users\\alguien\\Desktop"
    )
    ini.write_text(base, encoding="utf-8")
    _cfg, avisos = load_config(ini, capture_warnings=True)
    assert any("ruta_inexistente" in a and "anime_path" in a for a in avisos), avisos
