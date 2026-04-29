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


def reorder_tracks(mkv_path: Path, mkv_info: dict, config: dict, tool_paths: dict, chapter_file_path: Path | None = None) -> bool:
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

    latino_kws = config['LATINO_KEYWORDS']
    spain_kws = config['SPAIN_KEYWORDS']
    # Códigos que consideramos "Latino" por defecto si no hay info extra
    latino_codes = ['es-419', 'lat'] 
    generic_spanish_codes = ['spa', 'es']

    for track in tracks:
        tid = track['id']
        ttype = track['type']
        props = track.get('properties', {})
        lang = props.get('language', 'und')
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
            
            # 1. Chequeo por código estricto
            if lang in latino_codes:
                is_latino = True
            # 2. Chequeo por keywords en nombre (prioridad sobre código genérico 'spa')
            elif any(kw in name for kw in latino_kws):
                is_latino = True
            elif any(kw in name for kw in spain_kws):
                is_spain = True
            # 3. Genérico (spa/es) sin keywords específicas
            elif lang in generic_spanish_codes:
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

    # Archivos temporales
    temp_output = mkv_path.with_suffix('.reorder_temp.mkv')
    
    cmd = [
        tool_paths['mkvmerge'],
        '-o', str(temp_output),
        '--track-order', track_order_arg
    ]
    
    # Configurar Flags: Default y Forced
    # Lógica: 
    # - Primera pista Latino -> Default=Yes
    # - Si no hay Latino, Primera España -> Default=Yes
    # - Si no hay España, Primera Genérica -> Default=Yes
    # - Resto -> Default=No
    
    primary_sub = None
    if sub_latino: primary_sub = sub_latino[0]
    elif sub_spain: primary_sub = sub_spain[0]
    elif sub_generic_spanish: primary_sub = sub_generic_spanish[0]
    
    # Procesar flags para TODOS los subs para asegurar limpieza
    all_subs = sub_latino + sub_spain + sub_generic_spanish + sub_others
    for sub in all_subs:
        sid = sub['id']
        is_default = (sub == primary_sub)
        
        # Resetear flags: --default-track-flag ID:bool --forced-display-flag ID:no
        cmd.extend(['--default-track-flag', f"{sid}:{'yes' if is_default else 'no'}"])
        # Opcional: Resetear forced a no para evitar confusiones, a menos que se quiera preservar
        # cmd.extend(['--forced-display-flag', f"{sid}:no"]) 

    # Insertar capítulos si se generaron
    if chapter_file_path and chapter_file_path.exists():
        cmd.extend(['--chapters', str(chapter_file_path)])
        logging.info("[Chapters] Incluyendo capítulos en mkvmerge reorder: %s", chapter_file_path.name)
    cmd.append(str(mkv_path))

    try:
        logging.info("Ejecutando mkvmerge para reordenar...")
        # Loguear comando (ocultando rutas completas si es muy largo, pero aquí es útil ver todo)
        cmd_debug = ' '.join(f'"{c}"' if ' ' in c else c for c in cmd)
        logging.debug(f"Comando mkvmerge: {cmd_debug}")
        
        proc_reorder = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        # Reemplazar original
        if config['REPLACE_ORIGINAL_MKV']:
            logging.info("Reemplazando archivo original...")
            os.replace(temp_output, mkv_path)
            logging.info("¡Reordenamiento completado y archivo actualizado!")
        else:
            final_name = mkv_path.with_stem(mkv_path.stem + ".reordered")
            os.replace(temp_output, final_name)
            logging.info(f"Guardado como: {final_name}")
            
        return True

    except subprocess.CalledProcessError as e:
        logging.error(f"Error en mkvmerge reorder (Exit Code {e.returncode}).")
        logging.error(f"STDOUT:\n{e.stdout}")
        logging.error(f"STDERR:\n{e.stderr}")
        if temp_output.exists(): os.remove(temp_output)
        return False
    except Exception as e:
        logging.error(f"Error inesperado reordenando: {e}")
        if temp_output.exists(): os.remove(temp_output)
        return False
