"""
track_reorder.py — MKV track reordering with language priority.

Reorders subtitle and audio tracks in MKV files to prioritize
Latin American Spanish > European Spanish > Other languages.
Uses mkvmerge for the actual remux operation.
"""

import logging
import os
import subprocess
from pathlib import Path
from src.remux import reorder_and_save


def reorder_tracks(mkv_path: Path, mkv_info: dict, cfg, tool_paths: dict, chapter_file_path: Path | None = None) -> bool:
    """
    Reordena las pistas del MKV para priorizar Español Latino > Español España > Otros.
    Retorna True si se realizó el reordenamiento exitosamente.
    """
    logging.info("--- Iniciando Reordenamiento de Pistas ---")
    if not mkv_info or 'tracks' not in mkv_info:
        logging.error("No hay información de tracks para reordenar.")
        return False

    tracks = mkv_info['tracks']
    video_tracks = []
    audio_tracks = []
    sub_latino = []
    sub_spain = []
    sub_generic_spanish = [] # Nueva categoría
    sub_others = []
    other_tracks = [] # Kapitulos, tags, etc.

    latino_kws = cfg.latino_keywords
    spain_kws = cfg.spain_keywords
    # Códigos que consideramos "Latino" por defecto si no hay info extra
    latino_codes = ['es-419', 'lat'] 
    generic_spanish_codes = ['spa', 'es']

    for track in tracks:
        tid = track['id']
        ttype = track['type']
        props = track.get('properties', {})
        # Ambos códigos cuentan para los tiers: language_ietf ('es-419')
        # prioriza, pero el legacy ('spa') sigue clasificando aunque el IETF
        # traiga una variante no reconocida (p. ej. 'es-ES').
        lang_legacy = (props.get('language') or '').strip().lower()
        lang_ietf = (props.get('language_ietf') or '').strip().lower()
        langs = {c for c in (lang_ietf, lang_legacy) if c}
        name = props.get('track_name', '').lower()
        
        # Clasificación
        if ttype == 'video':
            video_tracks.append(track)
        elif ttype == 'audio':
            audio_tracks.append(track)
        elif ttype == 'subtitles':
            is_latino = False
            is_spain = False
            is_generic_spanish = False
            
            # 1. Chequeo por código estricto (IETF o legacy)
            if any(c in latino_codes for c in langs):
                is_latino = True
            # 2. Chequeo por keywords en nombre (prioridad sobre código genérico 'spa')
            elif any(kw in name for kw in latino_kws):
                is_latino = True
            elif any(kw in name for kw in spain_kws):
                is_spain = True
            # 3. Genérico (spa/es) sin keywords específicas
            elif any(c in generic_spanish_codes for c in langs):
                is_generic_spanish = True
            
            if is_latino:
                sub_latino.append(track)
            elif is_spain:
                sub_spain.append(track)
            elif is_generic_spanish:
                sub_generic_spanish.append(track)
            else:
                sub_others.append(track)
        else:
            other_tracks.append(track)

    # Orden final: Video -> Audio -> Sub Latino -> Sub España -> Sub Genérico -> Sub Otros -> Resto
    ordered_tracks = video_tracks + audio_tracks + sub_latino + sub_spain + sub_generic_spanish + sub_others + other_tracks
    
    # Comprobar si realmente necesitamos reordenar
    if not sub_latino and not sub_spain and not sub_generic_spanish:
        logging.warning("No se encontraron pistas de Español (Latino, España o Genérico) para priorizar.")
        return False

    # Construir track order para mkvmerge: 0:id1,0:id2,0:id3...
    # El formato correcto es FileID:TrackID para cada pista.
    track_order_pairs = [f"0:{t['id']}" for t in ordered_tracks]
    track_order_arg = ",".join(track_order_pairs)
    
    logging.info(f"Nuevo orden de pistas (IDs): {[t['id'] for t in ordered_tracks]}")
    logging.info(f"  Latino: {[t['id'] for t in sub_latino]}")
    logging.info(f"  España: {[t['id'] for t in sub_spain]}")
    logging.info(f"  Genérico: {[t['id'] for t in sub_generic_spanish]}")

    # Flags: Default y Forced
    # Lógica:
    # - Primera pista Latino -> Default=Yes
    # - Si no hay Latino, Primera España -> Default=Yes
    # - Si no hay España, Primera Genérica -> Default=Yes
    # - Resto -> Default=No
    primary_sub = None
    if sub_latino: primary_sub = sub_latino[0]
    elif sub_spain: primary_sub = sub_spain[0]
    elif sub_generic_spanish: primary_sub = sub_generic_spanish[0]
    
    # Flags de default para todos los subs: latino/españa/genérico primero, resto no
    default_flag_args = []
    all_subs = sub_latino + sub_spain + sub_generic_spanish + sub_others
    for sub in all_subs:
        sid = sub['id']
        is_default = (sub == primary_sub)
        default_flag_args += ['--default-track-flag', f"{sid}:{'yes' if is_default else 'no'}"]

    result = reorder_and_save(mkv_path, track_order_arg, default_flag_args,
                              cfg, tool_paths, chapters=chapter_file_path)
    for warn_msg in result.warnings:
        logging.warning("[Remux] %s", warn_msg)

    if result.ok:
        if cfg.replace_original_mkv:
            logging.info("¡Reordenamiento completado y archivo actualizado!")
        else:
            logging.info(f"Reordenamiento guardado como: {result.output.name}")
    else:
        logging.error(f"Reordenamiento falló: {'; '.join(result.warnings) or 'desconocido'}")
    return result.ok
