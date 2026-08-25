import os
import stat
import sys
import json
import urllib.request
import urllib.error
import zipfile
import io
import shutil
from pathlib import Path
import logging

def clean_old_files(script_dir: Path):
    """Borra restos '.old' de actualizaciones previas junto al script."""
    for path in script_dir.rglob('*.old'):
        try:
            path.unlink()
            logging.debug(f"Eliminado archivo antiguo: {path.name}")
        except Exception as e:
            logging.debug(f"No se pudo eliminar {path.name}: {e}")

def check_and_update(current_version: str, script_dir: Path) -> None:
    """Compara releases/latest con la versión local y aplica si es mayor."""
    logging.info("[Updater] Buscando actualizaciones en GitHub...")
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/fititoz/Generacion-Sub-AI/releases/latest",
            headers={"User-Agent": "Generacion_Sub_AI_Updater"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
        
        latest_tag = data.get("tag_name", "")
        if not latest_tag:
            return
        
        latest_version = latest_tag.lstrip('v')
        
        # Parse versions
        curr_parts = tuple(map(int, current_version.split('.')))
        latest_parts = tuple(map(int, latest_version.split('.')))
        if latest_parts > curr_parts:
            logging.info(f"[Updater] ¡Nueva versión disponible! ({current_version} -> {latest_version})")
            zip_url = data.get("zipball_url")
            if not zip_url:
                return

            logging.info("[Updater] Descargando actualización...")
            with urllib.request.urlopen(zip_url, timeout=30) as zip_resp:
                with zipfile.ZipFile(io.BytesIO(zip_resp.read())) as z:
                    root_folder = z.namelist()[0].split('/')[0]

                    import tempfile
                    with tempfile.TemporaryDirectory() as tmpdir:
                        z.extractall(tmpdir)
                        source_dir = Path(tmpdir) / root_folder

                        logging.info("[Updater] Aplicando archivos nuevos...")
                        for src_file in source_dir.rglob('*'):
                            if src_file.is_file():
                                rel_path = src_file.relative_to(source_dir)
                                dst_file = script_dir / rel_path
                                dst_file.parent.mkdir(parents=True, exist_ok=True)

                                # Los zipballs de GitHub no preservan bits Unix:
                                # sin esto el entry pierde +x y Sonarr no puede
                                # ejecutarlo (Permission denied post-update).
                                is_entry = (rel_path.parent == Path('.') and
                                            rel_path.name == 'Generacion_Sub_AI.py')
                                old_mode = (stat.S_IMODE(dst_file.stat().st_mode)
                                            if dst_file.exists() else None)

                                def _apply_mode():
                                    if is_entry:
                                        os.chmod(dst_file, 0o755)
                                    elif old_mode is not None:
                                        os.chmod(dst_file, old_mode)

                                try:
                                    shutil.copy2(src_file, dst_file)
                                    _apply_mode()
                                except PermissionError:
                                    if dst_file.exists():
                                        # Truco para archivos bloqueados en Windows
                                        old_file = dst_file.with_suffix(dst_file.suffix + '.old')
                                        if old_file.exists():
                                            try:
                                                old_file.unlink()
                                            except: pass
                                        dst_file.rename(old_file)
                                        shutil.copy2(src_file, dst_file)
                                        _apply_mode()

            logging.info("[Updater] Actualización aplicada con éxito. Reiniciando script...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            logging.info("[Updater] Ya tienes la versión más reciente.")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logging.info("[Updater] Sin releases publicados (404). Usando versión actual.")
        else:
            logging.warning(f"[Updater] Error al comprobar actualización (HTTP {e.code}): {e}")
    except Exception as e:
        logging.warning(f"[Updater] Error al comprobar/aplicar actualización: {e}")
