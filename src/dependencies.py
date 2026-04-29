"""
dependencies.py — Runtime dependency checker and auto-installer.

Verifies required pip packages at startup using importlib.metadata.
In interactive mode, offers to install missing packages automatically.
In headless mode (Docker/Sonarr), fails fast with clear error messages.
"""
import sys
import subprocess
import importlib.metadata
from src.constants import REQUIRED_PACKAGES

def check_and_install_dependencies():
    missing_packages = []
    print("--- Verificando Dependencias (usando importlib.metadata) ---")
    for import_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            version = importlib.metadata.version(pip_name)
            print(f"  Paquete encontrado: {pip_name} (Versión: {version})")
        except importlib.metadata.PackageNotFoundError:
            print(f"  Paquete faltante: {pip_name}")
            missing_packages.append(pip_name)
        except Exception as e:
            print(f"  ERROR al verificar {pip_name}: {e}", file=sys.stderr)
            missing_packages.append(pip_name)

    if missing_packages:
        print("\nFaltan paquetes necesarios.")
        if not sys.stdin.isatty():
            # Modo headless (Sonarr, Docker, etc.) — fail fast, sin input()
            print(f"ERROR: Modo headless detectado. Paquetes faltantes: {', '.join(missing_packages)}", file=sys.stderr)
            print("Instale manualmente: python3 -m pip install " + ' '.join(missing_packages), file=sys.stderr)
            return False
        install = input("¿Instalar ahora con pip? (s/N): ").lower()
        if install == 's':
            python_executable = sys.executable
            installation_successful = True
            packages_to_install = list(set(missing_packages))
            print(f"Intentando instalar: {', '.join(packages_to_install)}")
            for package in packages_to_install:
                print(f"\nInstalando {package}...");
                try:
                    subprocess.check_call([python_executable, "-m", "pip", "install", "--upgrade", package])
                    print(f"¡{package} OK!")
                except Exception as e:
                    print(f"ERROR instalando {package}: {e}", file=sys.stderr)
                    installation_successful = False
            if not installation_successful:
                print("\nHubo errores instalación.", file=sys.stderr)
                return False
            print("\nInstalación completa. Re-verificando...");
            final_missing = [];
            for import_name, pip_name in REQUIRED_PACKAGES.items():
                 try:
                     importlib.metadata.version(pip_name)
                 except importlib.metadata.PackageNotFoundError:
                     final_missing.append(pip_name)
            if final_missing:
                print(f"ERROR: Aún faltan: {', '.join(final_missing)}", file=sys.stderr)
                return False
            else:
                print("Dependencias OK post-instalación.")
                return True
        else:
            print("Instalación cancelada.", file=sys.stderr)
            return False
    else:
        print("Dependencias requeridas encontradas.")
        return True


def check_and_install_chapter_deps():
    """
    Verifica e instala automáticamente las dependencias de generación de capítulos.
    Retorna True si todas están disponibles, False si fallan.
    Se ejecuta SOLO cuando CHAPTERS_ENABLED=True.
    """
    from src.constants import CHAPTER_PACKAGES
    
    missing = []
    for import_name, pip_name in CHAPTER_PACKAGES.items():
        try:
            importlib.metadata.version(pip_name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(pip_name)
        except Exception:
            missing.append(pip_name)
    
    if not missing:
        return True
    
    print(f"[Chapters] Paquetes faltantes para capítulos: {', '.join(missing)}")
    print(f"[Chapters] Instalando automáticamente con pip...")
    
    python_executable = sys.executable
    for package in missing:
        try:
            subprocess.check_call(
                [python_executable, "-m", "pip", "install", "--upgrade", package],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            print(f"[Chapters] {package} instalado OK.")
        except Exception as e:
            print(f"[Chapters] ERROR instalando {package}: {e}", file=sys.stderr)
            return False
    
    # Re-verificar
    for import_name, pip_name in CHAPTER_PACKAGES.items():
        try:
            importlib.metadata.version(pip_name)
        except importlib.metadata.PackageNotFoundError:
            print(f"[Chapters] ERROR: {pip_name} aún no disponible tras instalación.", file=sys.stderr)
            return False
    
    print("[Chapters] Todas las dependencias de capítulos instaladas correctamente.")
    return True