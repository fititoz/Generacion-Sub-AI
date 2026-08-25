"""
remux.py — Ejecución mkvmerge unificada: exit-codes, validación, atomicidad.

Un solo núcleo (`run_merge`) posee todo lo que antes estaba replicado con
criterios distintos en tres sitios del producto:

- Exit codes oficiales de mkvmerge: rc=0 OK · rc=1 ÉXITO con warnings
  registrados (salida válida; era la causa del bucle infinito de reencolado)
  · rc>=2 fallo.
- Timeout único REMUX_TIMEOUT_S.
- Temporal único en el mismo directorio (`.<propósito>.tmp.mkv`) + `os.replace`
  (atómico) hacia el destino; borrado garantizado del temporal en cualquier
  fallo.
- Validación: ligera siempre (existe + tamaño > 0); profunda vía probe `-J`
  sobre la salida (parsea, contiene video, duración dentro de ±10s del
  original) cuando el destino es el archivo original (reemplazo destructivo).

Los envoltorios (`embed_translation`, `embed_chapters`, `reorder_and_save`)
conservan el vocabulario de cada llamador; la política de qué pistas/flags/
orden corresponde a los callers, no a este módulo.
"""
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

REMUX_TIMEOUT_S = 900
PROBE_TIMEOUT_S = 60
DURATION_TOLERANCE_S = 10.0


@dataclass(frozen=True)
class RemuxResult:
    """Resultado inmutable de una operación mkvmerge."""
    ok: bool
    warnings: tuple = ()
    output: Path | None = None


def _probe_info(path: Path, tools: dict):
    """`mkvmerge -J` sobre path; dict JSON o None si falla."""
    try:
        proc = subprocess.run(
            [tools["mkvmerge"], "-J", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=PROBE_TIMEOUT_S,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)
    except Exception:
        return None


def _duration_s(info) -> float | None:
    """Duración del contenedor en segundos (tolera reporte en nanosegundos)."""
    try:
        d = info["container"]["properties"]["duration"]
    except (KeyError, TypeError):
        return None
    if d is None:
        return None
    d = float(d)
    if d > 1e7:          # algunos builds lo reportan en nanosegundos
        d /= 1e9
    return d


def _has_video(info) -> bool:
    """True si el JSON de -J contiene al menos una pista de video."""
    return any(t.get("type") == "video" for t in info.get("tracks", []))


def _cleanup(temp_out: Path):
    """Borra el temporal ignorando errores de limpieza."""
    try:
        if temp_out.exists():
            temp_out.unlink()
    except OSError as e:
        logging.warning("[Remux] No se pudo eliminar temporal %s: %s", temp_out, e)


def run_merge(tools: dict, args: list, temp_out: Path, dest: Path, *,
              destructive: bool, source: Path, purpose: str) -> RemuxResult:
    """
    Ejecuta `[mkvmerge, -o temp, *args]`, valida y promueve a `dest`.

    - destructive=True implica validación profunda (-J) antes del os.replace.
      `source` es el original que sería pisado (para comparar duración).
    """
    cmd = [tools["mkvmerge"], "-o", str(temp_out)] + [str(a) for a in args]
    logging.debug("[Remux:%s] Cmd: %s", purpose, " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=REMUX_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        logging.error("[Remux:%s] timeout tras %ds", purpose, REMUX_TIMEOUT_S)
        _cleanup(temp_out)
        return RemuxResult(False, (f"timeout {REMUX_TIMEOUT_S}s",), None)
    except Exception as e:
        logging.exception("[Remux:%s] error ejecutando mkvmerge", purpose)
        _cleanup(temp_out)
        return RemuxResult(False, (str(e),), None)

    err_lines = [l for l in (proc.stderr or "").splitlines() if l.strip()]
    rc = proc.returncode

    if rc == 1:
        msg = "mkvmerge terminó con warnings (rc=1, salida válida): " + " | ".join(err_lines[-5:])
        logging.warning("[Remux:%s] %s", purpose, msg)
        warnings = (msg,)
    elif rc != 0:
        tail = "\n".join(err_lines[-10:]) or "(sin stderr)"
        logging.error("[Remux:%s] falló rc=%d. STDERR:\n%s", purpose, rc, tail)
        _cleanup(temp_out)
        return RemuxResult(False, (f"rc={rc}", tail.splitlines()[-1] if err_lines else ""), None)
    else:
        warnings = ()

    # --- Validación ligera (siempre) ---
    if not temp_out.exists() or temp_out.stat().st_size == 0:
        logging.error("[Remux:%s] salida ausente o vacía", purpose)
        _cleanup(temp_out)
        return RemuxResult(False, ("salida ausente/vacía",), None)

    # --- Validación profunda (antes de pisar el original) ---
    if destructive:
        out_info = _probe_info(temp_out, tools)
        if out_info is None:
            logging.error("[Remux:%s] salida no parseable por -J; abortando reemplazo", purpose)
            _cleanup(temp_out)
            return RemuxResult(False, ("salida no parseable",), None)
        if not _has_video(out_info):
            logging.error("[Remux:%s] salida sin pista de video; abortando reemplazo", purpose)
            _cleanup(temp_out)
            return RemuxResult(False, ("sin video en salida",), None)

        src_info = _probe_info(source, tools)
        d_src = _duration_s(src_info) if src_info else None
        d_out = _duration_s(out_info)
        if d_src is not None and d_out is not None and abs(d_src - d_out) > DURATION_TOLERANCE_S:
            logging.error("[Remux:%s] duración difiere (%.1fs vs %.1fs); abortando reemplazo",
                          purpose, d_out, d_src)
            _cleanup(temp_out)
            return RemuxResult(False, (f"duración {d_out:.1f}s vs {d_src:.1f}s",), None)

    os.replace(temp_out, dest)
    return RemuxResult(True, warnings, dest)


# ---------------------------------------------------------------------------
# Envoltorios por dominio
# ---------------------------------------------------------------------------

def embed_translation(source: Path, sub_path: Path, cfg, tools: dict,
                      chapters: Path | None = None) -> RemuxResult:
    """Incrusta el subtítulo traducido (+ capítulos opcionales) en el MKV."""
    replace = cfg.replace_original_mkv
    temp = source.with_suffix(source.suffix + ".traducido.tmp.mkv")
    dest = source if replace else source.with_stem(source.stem + cfg.output_mkv_suffix)

    args = []
    if chapters and Path(chapters).exists():
        args += ["--chapters", str(chapters)]
    args.append(str(source))
    args += [
        "--language", f"0:{cfg.primary_target_code}",
        "--track-name", f"0:{cfg.translated_track_name}",
        "--default-track-flag", f"0:{'yes' if cfg.set_new_sub_default else 'no'}",
        str(sub_path),
    ]
    return run_merge(tools, args, temp, dest,
                     destructive=replace, source=source, purpose="traducido")


def embed_chapters(source: Path, chapter_file: Path, cfg, tools: dict) -> RemuxResult:
    """Incrusta capítulos en un MKV existente (sin tocar pistas)."""
    replace = cfg.replace_original_mkv
    temp = source.with_suffix(".chapters.tmp.mkv")
    dest = source if replace else source.with_stem(source.stem + ".chapters")
    args = ["--chapters", str(chapter_file), str(source)]
    return run_merge(tools, args, temp, dest,
                     destructive=replace, source=source, purpose="chapters")


def reorder_and_save(source: Path, track_order_arg: str, default_flag_args: list,
                     cfg, tools: dict, chapters: Path | None = None) -> RemuxResult:
    """Aplica un orden de pistas ya decidido por el caller (+ flags default)."""
    replace = cfg.replace_original_mkv
    temp = source.with_suffix(".reorder.tmp.mkv")
    dest = source if replace else source.with_stem(source.stem + ".reordered")
    args = ["--track-order", track_order_arg, *default_flag_args]
    if chapters and Path(chapters).exists():
        args += ["--chapters", str(chapters)]
    args.append(str(source))
    return run_merge(tools, args, temp, dest,
                     destructive=replace, source=source, purpose="reorder")
