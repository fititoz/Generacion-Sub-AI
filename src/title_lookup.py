"""
title_lookup.py — Anime title lookup via animetitles.xml (anime-lists).

Downloads animetitles.xml from GitHub, caches it with ETag for conditional
refreshes, parses XML to build an English→Romaji lookup dictionary.
All functions return None on failure — ZERO exceptions propagate to the caller.
"""

import logging
import os
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict
from src.constants import ANIMETITLES_PARSED_CACHE_FILENAME, TITLE_CACHE_TTL_SECONDS
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ANIMETITLES_URL = 'https://raw.githubusercontent.com/Anime-Lists/anime-lists/refs/heads/master/animetitles.xml'
ANIMETITLES_FILENAME = 'animetitles.xml'
ANIMETITLES_ETAG_FILENAME = 'animetitles.etag'
XML_LANG_ATTR = '{http://www.w3.org/XML/1998/namespace}lang'
# Characters that should be treated as equivalent for title matching
# Sonarr may send ' (U+0027) but XML has ` (U+0060), or curly quotes, etc.
_QUOTE_NORMALIZE = str.maketrans({
    '\u0060': "'",   # backtick -> apostrophe
    '\u2018': "'",   # left single curly quote
    '\u2019': "'",   # right single curly quote
    '\u201C': '"',   # left double curly quote
    '\u201D': '"',   # right double curly quote
    '\u2033': '"',   # double prime
    '\u2032': "'",   # prime
    '\u00B4': "'",   # acute accent
    '\uFF07': "'",   # fullwidth apostrophe
    '\uFF02': '"',   # fullwidth quotation mark
})


def _normalize_title(title: str) -> str:
    """Normalize a title for fuzzy matching: lowercase, normalize quotes."""
    return title.strip().lower().translate(_QUOTE_NORMALIZE)

# Module-level cache — persists for the lifetime of the process
_title_cache: Optional[Dict[str, str]] = None  # english_lower -> romaji


def _load_parsed_cache(xml_path: Path, cache_dir: Path) -> Optional[Dict[str, str]]:
    """Load parsed title dict from JSON cache if valid (TTL + XML fingerprint match)."""
    cache_path = cache_dir / ANIMETITLES_PARSED_CACHE_FILENAME
    if not cache_path.exists():
        return None

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Check TTL
        timestamp = data.get('timestamp', 0)
        if time.time() - timestamp > TITLE_CACHE_TTL_SECONDS:
            logging.info("[TitleLookup] Caché parseado expirado (TTL=%ds). Re-parseando.", TITLE_CACHE_TTL_SECONDS)
            return None

        # Check XML fingerprint (mtime + size)
        fingerprint = data.get('xml_fingerprint', {})
        if xml_path.exists():
            stat = xml_path.stat()
            if (abs(stat.st_mtime - fingerprint.get('mtime', 0)) > 0.01
                    or stat.st_size != fingerprint.get('size', 0)):
                logging.info("[TitleLookup] XML cambió (mtime/size). Re-parseando.")
                return None

        lookup = data.get('lookup', {})
        if not lookup:
            return None

        logging.info("[TitleLookup] Caché parseado cargado (%d títulos, edad=%.0fs).",
                     len(lookup), time.time() - timestamp)
        return lookup

    except (json.JSONDecodeError, KeyError, OSError) as e:
        logging.warning("[TitleLookup] Error leyendo caché parseado: %s", e)
        return None


def _save_parsed_cache(lookup: Dict[str, str], xml_path: Path, cache_dir: Path) -> None:
    """Save parsed title dict as JSON with XML fingerprint for cross-invocation reuse."""
    cache_path = cache_dir / ANIMETITLES_PARSED_CACHE_FILENAME
    tmp_path = cache_path.with_suffix('.tmp')

    try:
        fingerprint = {}
        if xml_path.exists():
            stat = xml_path.stat()
            fingerprint = {'mtime': stat.st_mtime, 'size': stat.st_size}

        data = {
            'timestamp': time.time(),
            'xml_fingerprint': fingerprint,
            'lookup': lookup,
        }

        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

        os.replace(str(tmp_path), str(cache_path))
        logging.info("[TitleLookup] Caché parseado guardado (%d títulos).", len(lookup))

    except Exception as e:
        logging.warning("[TitleLookup] Error guardando caché parseado: %s", e)
        if tmp_path.exists():
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _download_animetitles(cache_dir: Path) -> Optional[Path]:
    """Download or refresh animetitles.xml using ETag conditional caching."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    xml_path = cache_dir / ANIMETITLES_FILENAME
    etag_path = cache_dir / ANIMETITLES_ETAG_FILENAME
    tmp_path = xml_path.with_suffix('.tmp')

    try:
        req = Request(ANIMETITLES_URL)
        if etag_path.exists():
            stored_etag = etag_path.read_text(encoding='utf-8').strip()
            if stored_etag:
                req.add_header('If-None-Match', stored_etag)

        response = urlopen(req, timeout=30)
        # HTTP 200 — new data
        data = response.read()
        with open(tmp_path, 'wb') as f:
            f.write(data)

        if tmp_path.stat().st_size < 1000:
            logging.warning("[TitleLookup] animetitles.xml descargado pero demasiado pequeño. Ignorando.")
            return xml_path if xml_path.exists() else None

        os.replace(tmp_path, xml_path)

        # Save ETag
        new_etag = response.headers.get('ETag', '')
        if new_etag:
            etag_path.write_text(new_etag, encoding='utf-8')

        size_mb = xml_path.stat().st_size / 1048576
        logging.info("[TitleLookup] animetitles.xml descargado (%.1f MB).", size_mb)
        return xml_path

    except HTTPError as e:
        if e.code == 304:
            logging.info("[TitleLookup] animetitles.xml sin cambios (304).")
            return xml_path if xml_path.exists() else None
        logging.warning("[TitleLookup] Error HTTP descargando animetitles.xml: %s", e)
        return xml_path if xml_path.exists() else None
    except Exception as e:
        logging.warning("[TitleLookup] Error descargando animetitles.xml: %s", e)
        return xml_path if xml_path.exists() else None
    finally:
        if tmp_path.exists():
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _parse_animetitles_xml(xml_path: Path) -> Dict[str, str]:
    """Parse animetitles.xml into a dict mapping english_title_lower -> romaji_main_title."""
    lookup: Dict[str, str] = {}

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()

        for anime_elem in root.findall('anime'):
            # Find main title (romanized Japanese)
            main_title = None
            for title_elem in anime_elem.findall('title'):
                if (title_elem.get('type') == 'main'
                        and title_elem.get(XML_LANG_ATTR) == 'x-jat'
                        and title_elem.text):
                    main_title = title_elem.text.strip()
                    break

            if not main_title:
                continue

            # Index all English official titles and synonyms
            for title_elem in anime_elem.findall('title'):
                title_type = title_elem.get('type', '')
                lang = title_elem.get(XML_LANG_ATTR, '')

                if lang == 'en' and title_type in ('official', 'syn') and title_elem.text:
                    en_key = _normalize_title(title_elem.text)
                    if en_key:
                        lookup[en_key] = main_title

        logging.info("[TitleLookup] animetitles.xml parseado: %d títulos indexados.", len(lookup))

    except ET.ParseError as e:
        logging.warning("[TitleLookup] Error parseando animetitles.xml: %s", e)
    except Exception as e:
        logging.warning("[TitleLookup] Error inesperado parseando animetitles.xml: %s", e)

    return lookup


def lookup_romaji_title(english_title: str, cache_dir: Path) -> Optional[str]:
    """
    Look up the romanized Japanese (x-jat) title for an English anime title.
    Downloads/caches animetitles.xml if needed. Returns None if not found.
    """
    global _title_cache

    if _title_cache is None:
        xml_path = cache_dir / ANIMETITLES_FILENAME
        # Try disk cache first (skip expensive XML parse)
        cached = _load_parsed_cache(xml_path, cache_dir)
        if cached is not None:
            _title_cache = cached
        else:
            # Cache miss — download XML (ETag-aware) then parse
            xml_path = _download_animetitles(cache_dir)
            if xml_path is None or not xml_path.exists():
                _title_cache = {}
                return None
            _title_cache = _parse_animetitles_xml(xml_path)
            _save_parsed_cache(_title_cache, xml_path, cache_dir)

    key = _normalize_title(english_title)
    result = _title_cache.get(key)

    if result:
        logging.info("[TitleLookup] '%s' → '%s' (romaji)", english_title, result)
    else:
        logging.info("[TitleLookup] '%s' no encontrado en animetitles.xml. Usando título original.", english_title)

    return result
