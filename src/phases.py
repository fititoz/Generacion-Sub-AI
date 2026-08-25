"""
phases.py - Fases testeables del pipeline por archivo.

Unidades extraidas de process_file para que cada fase sea invocable y
testeable de forma independiente (sin importar el entry script, que tiene
efectos al importar). El orquestador solo secuencia y decide.
"""
import json
import logging
import subprocess
from pathlib import Path

import pysubs2

from src.protocol import is_error_sentinel, make_error


def probe_mkv_info(mkv_path: Path, tool_paths: dict):
    """PASO 2: mkvmerge -J -> (mkv_info|None, {tid: codec_id})."""
    track_codecs = {}
    mkv_info = None
    if not (tool_paths and tool_paths.get('mkvmerge')):
        logging.info("mkvmerge no disponible.")
        return None, track_codecs
    try:
        logging.debug("Obteniendo info detallada (mkvmerge -J)...")
        mkvmerge_cmd = [tool_paths['mkvmerge'], '-J', str(mkv_path)]
        result = subprocess.run(mkvmerge_cmd, capture_output=True, text=True, check=True,
                                encoding='utf-8', errors='replace', timeout=60)
        mkv_info = json.loads(result.stdout)
        logging.debug("JSON parseado.")
        if 'tracks' in mkv_info:
            for track_data in mkv_info['tracks']:
                tid = track_data.get('id')
                props = track_data.get('properties', {})
                codec_id = props.get('codec_id')
                if tid is not None and codec_id:
                    track_codecs[tid] = codec_id
                    logging.debug("  -> ID %d: Codec '%s'.", tid, codec_id)
    except subprocess.TimeoutExpired:
        logging.warning("Timeout obteniendo info detallada.")
    except subprocess.CalledProcessError as e:
        logging.warning(f"mkvmerge -J falló (código {e.returncode}): {e.stderr or e.stdout or 'Sin salida'}")
    except json.JSONDecodeError as e:
        logging.warning(f"Error decodificando JSON: {e}")
        logging.debug("Output mkvmerge:\n%s", result.stdout if 'result' in locals() else "N/A")
    except Exception as e:
        logging.warning(f"Fallo inesperado info detallada: {e}", exc_info=False)
    return mkv_info, track_codecs


def load_mkv_structure(mkv_path: Path, tool_paths: dict):
    """PASO 3: parseo pymkv. Retorna MKVFile o None en error fatal."""
    logging.debug("Analizando estructura pymkv: %s...", mkv_path.name)
    try:
        mkv = pysubs2_none_guard(mkv_path)
    except Exception:
        logging.exception("Error fatal análisis pymkv:")
        return None
    if tool_paths and tool_paths.get('mkvmerge'):
        try:
            mkv.mkvmerge_path = tool_paths['mkvmerge']
        except Exception as e_assign:
            logging.warning(f"No se pudo asignar mkvmerge_path a pymkv: {e_assign}")
    logging.debug("Análisis pymkv OK.")
    return mkv


def pysubs2_none_guard(mkv_path):
    """Import diferido de pymkv isolado para pruebas sin la dependencia."""
    from pymkv import MKVFile
    return MKVFile(str(mkv_path))


def scan_tracks_for_target(mkv, cfg):
    """PASO 4: lista pistas y detecta si ya existe la pista objetivo."""
    subs_tracks = []
    target_lang_found = False
    logging.info("Pistas encontradas:")
    target_codes_set = cfg.target_language_codes_set
    target_name_display = cfg.target_language_name
    latino_kws = cfg.latino_keywords
    spain_kws = cfg.spain_keywords
    target_is_latino = any(kw in target_name_display.lower() for kw in latino_kws)
    target_is_spain = any(kw in target_name_display.lower() for kw in spain_kws)

    for i, track in enumerate(mkv.tracks):
        tid = getattr(track, 'track_id', '?')
        ttype = getattr(track, 'track_type', '?')
        lang = getattr(track, 'language', 'und')
        codec_id = getattr(track, 'codec_id', '?')
        name = getattr(track, 'track_name', '?')
        default = getattr(track, 'default_track', False)
        forced = getattr(track, 'forced_track', False)

        codec = codec_id
        lang = lang if lang else 'und'
        track_name_lower = (name or '').lower()

        name_part = f", N='{name}'" if name else ""
        details = (
            f"  - Pista {i}: ID={tid}, T='{ttype}', L='{lang}', C='{codec}'"
            f"{name_part}"
            f"{' (Def)' if default else ''}"
            f"{' (Forz)' if forced else ''}"
        )
        logging.info(details)

        if ttype == 'subtitles':
            subs_tracks.append(track)
            is_target_variant = False
            if lang in target_codes_set:
                is_target_variant = True
                logging.info(f" --> Coincide código idioma ({lang}).")
            elif lang in ['spa', 'es'] and (target_is_latino or target_is_spain):
                track_is_latino = any(kw in track_name_lower for kw in latino_kws)
                track_is_spain = any(kw in track_name_lower for kw in spain_kws)
                if target_is_latino and track_is_latino:
                    is_target_variant = True
                    logging.info(f" -> Coincide variante Latino ('{name}').")
                elif target_is_spain and track_is_spain:
                    is_target_variant = True
                    logging.info(f" -> Coincide variante España ('{name}').")
            if is_target_variant:
                logging.info(" --> ¡Encontrada pista objetivo!")
                target_lang_found = True

    return subs_tracks, target_lang_found


def extract_source_subtitle(mkv_path: Path, track_id, source_ext: str, tmpdir: Path, tools: dict) -> Path:
    """PASO 8: mkvextract de la pista fuente a tmpdir. Lanza si falla/vacío."""
    import time
    t_extract = time.time()
    tmp_sub_extracted = Path(tmpdir) / f"track_{track_id}_source{source_ext}"
    cmd_extract = [tools['mkvextract'], str(mkv_path), 'tracks', f'{track_id}:{str(tmp_sub_extracted)}']
    logging.debug("Ejecutando mkvextract...")
    try:
        subprocess.run(cmd_extract, check=True, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=180)
        logging.info("Extracción OK (%.1fs).", time.time() - t_extract)
    except Exception:
        logging.exception("Fallo extracción mkvextract")
        raise
    if not tmp_sub_extracted.exists() or tmp_sub_extracted.stat().st_size == 0:
        raise RuntimeError("Extracted file empty/missing")
    return tmp_sub_extracted


def load_subtitles(path: Path):
    """PASO 9: carga con cascada de encodings. -> (subs, output_format 'srt'|'ass')."""
    loaded = False
    subs = None
    encs = ['utf-8', 'utf-8-sig', 'utf-16', 'latin-1', 'cp1252']
    for enc in encs:
        try:
            subs = pysubs2.load(str(path), encoding=enc)
            logging.info("Cargado OK (enc '%s', fmt '%s').", enc, subs.format)
            loaded = True
            break
        except Exception:
            pass
    if not loaded or subs is None:
        raise ValueError("Subtitle load failed")
    output_format = 'ass' if subs.format == 'ass' else 'srt'
    return subs, output_format


def collect_translatable(subs):
    """Líneas no-comment con texto. -> (líneas, {pos:i}, [índices originales])"""
    lines_to_translate = []
    line_indices_map = {}
    original_subs_indices = []
    for i, line in enumerate(subs):
        if not line.is_comment and line.text.strip():
            line_indices_map[len(lines_to_translate)] = i
            lines_to_translate.append(line.text)
            original_subs_indices.append(i)
    return lines_to_translate, line_indices_map, original_subs_indices


def translate_lines(api_client, cache, originals):
    """PASO 10: traducción con fallback recursivo; fallo crítico -> sentinela fatal."""
    try:
        return api_client.translate_recursive_fallback(originals, cache)
    except Exception as e:
        logging.error(f"Fallo crítico en traducción recursiva: {e}")
        return [make_error("ERROR_FATAL_TRADUCTOR")] * len(originals)


def apply_results(subs, line_indices_map, results) -> dict:
    """Escribe resultados en los eventos; los centinelas se saltan. -> stats."""
    stats = {'ok': 0, 'errors': 0}
    for pos, final_text in enumerate(results):
        original_index = line_indices_map[pos]
        if not is_error_sentinel(final_text):
            subs[original_index].text = final_text.replace('\n', '\\N')
            stats['ok'] += 1
        else:
            stats['errors'] += 1
            logging.warning("Error persistente línea [%d]: %s", original_index, final_text)
    return stats
