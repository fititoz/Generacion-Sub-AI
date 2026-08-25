"""
tests/test_cache_and_corrections.py — Fixes B-CACHE-IO S1 + B-CORRECT S1.

1. TranslationCache.set() RECHAZA valores con placeholders (antes logueaba
   y guardaba igual, envenenando corridas futuras).
2. TranslationCorrector invalida la entrada stale antes de corregir y
   re-cachea la corrección exitosa (antes: corrección neutralizada por el
   caché y costo API recurrente en cada corrida).
"""
from src.cache_manager import TranslationCache
from src.translation_validator import (
    TranslationCorrector,
    ValidationResult,
)


class FakeAPI:
    """Duck-typed APIClient: graba llamadas y devuelve correcciones fijas."""

    def __init__(self, batch_result=None, single_prefix="SINGLE"):
        self.batch_calls = []
        self.single_calls = []
        self.batch_result = batch_result
        self.single_prefix = single_prefix

    def _call_api_batch(self, cleaned_texts):
        self.batch_calls.append(list(cleaned_texts))
        if self.batch_result is None:
            raise RuntimeError("batch roto a propósito")
        return [self.batch_result] * len(cleaned_texts)

    def translate_single(self, text, cache=None):
        self.single_calls.append(text)
        return f"{self.single_prefix}: {text}"


def _result(idx, original, translated):
    return ValidationResult(
        line_index=idx,
        original=original,
        translated=translated,
        issues=["Translation suspiciously short (ratio 0.10)",
                "Low content ratio, no tags (ratio 0.10)"],
        severity="error",
    )


# --- Guard de placeholders -------------------------------------------------

def test_set_rechaza_placeholders():
    cache = TranslationCache(enable_cache=True)
    cache.set("k1", "__TAG0__ texto sin restaurar")
    assert "k1" not in cache          # antes: se guardaba igual (S1)
    cache.set("k2", "texto limpio")
    assert cache.get("k2") == "texto limpio"


def test_delete_invalida():
    cache = TranslationCache(enable_cache=True)
    cache.set("k", "v")
    assert "k" in cache
    cache.delete("k")
    assert "k" not in cache


# --- Correcciones ----------------------------------------------------------

ORIG = "El reloj marcaba 21:45"
BAD = "corto"


def test_correccion_batch_re_cachea_y_no_relee_stale():
    cache = TranslationCache(enable_cache=True)
    cache.set(ORIG, BAD)                       # entrada stale pre-sembrada
    fake = FakeAPI(batch_result="CORREGIDO en el reloj 21:45")
    corrector = TranslationCorrector(fake, cache)
    out = corrector.attempt_corrections([_result(0, ORIG, BAD)], [BAD])

    assert out[0].startswith("CORREGIDO")      # corrección aplicada
    assert cache.get(ORIG) == out[0]           # fix re-cacheado (antes no)
    # y lo corregido NO es la entrada stale
    assert cache.get(ORIG) != BAD


def test_fallback_single_bypasa_la_entrada_stale():
    cache = TranslationCache(enable_cache=True)
    cache.set(ORIG, BAD)                       # stale que antes neutralizaba
    fake = FakeAPI()                           # _call_api_batch lanza → fallback
    corrector = TranslationCorrector(fake, cache)
    out = corrector.attempt_corrections([_result(0, ORIG, BAD)], [BAD])

    assert out[0].startswith("SINGLE:")
    assert fake.single_calls == [ORIG]         # re-tradujo pese al caché
    assert ORIG not in cache                   # stale invalidada antes de corregir
    # (la persistencia del fresh la hace el APIClient real vía set())


def test_sin_issues_no_toca_nada():
    cache = TranslationCache(enable_cache=True)
    cache.set(ORIG, "buena")
    fake = FakeAPI(batch_result="X")
    corrector = TranslationCorrector(fake, cache)
    ok = ValidationResult(line_index=0, original=ORIG,
                          translated="buena", issues=[], severity="info")
    out = corrector.attempt_corrections([ok], ["buena"])
    assert out == ["buena"] and fake.batch_calls == []
