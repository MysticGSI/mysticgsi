"""Qualcomm QFIL rawprogram XML partition assembler."""

from typing import Dict, List, Optional, Set, Tuple
import glob
import os
from xml.etree import ElementTree

from . import sparse

DEFAULT_SECTOR_SIZE = 512


def is_qfil_dir(directory: str) -> bool:
    return bool(glob.glob(os.path.join(directory, "rawprogram*.xml")))


def _programs(xml_paths):
    """Yields (label, start_sector, sector_size, filename) entries."""
    for xml_path in xml_paths:
        try:
            root = ElementTree.parse(xml_path).getroot()
        except ElementTree.ParseError:
            continue
        for prog in root.iter('program'):
            filename = prog.get('filename', '')
            if not filename or filename == 'NONE':
                continue
            label = (prog.get('label') or filename.split('.')[0]).lower()
            try:
                start = int(prog.get('start_sector', '0'))
                sector_size = int(prog.get('SECTOR_SIZE_IN_BYTES',
                                           DEFAULT_SECTOR_SIZE))
            except ValueError:
                continue
            yield label, start, sector_size, filename


def _assemble(pieces, out_path: str):
    """Writes each (start_sector, sector_size, path) piece at its offset."""
    first = min(start for start, _, _ in pieces)
    # A piece may itself be named <part>.img, so build next to it.
    tmp_path = f"{out_path}.qfil.tmp"
    end = 0
    with open(tmp_path, 'wb') as out_f:
        for start, sector_size, path in sorted(pieces):
            with open(path, 'rb') as in_f:
                offset = (start - first) * sector_size
                end = max(end, sparse.expand(in_f, out_f, offset))
        out_f.truncate(end)
    os.replace(tmp_path, out_path)
    for _, _, path in pieces:
        if path != out_path and os.path.exists(path):
            os.remove(path)


def process_qfil(
    directory: str,
    output_dir: str,
    target_partitions: Optional[Set[str]] = None,
    logger=None
) -> List[str]:
    os.makedirs(output_dir, exist_ok=True)
    xml_files = sorted(
        glob.glob(os.path.join(directory, "rawprogram*.xml")))
    preferred = [x for x in xml_files
                 if "unsparse" in os.path.basename(x).lower()]

    targets = ({p.lower() for p in target_partitions}
               if target_partitions else None)
    groups: Dict[str, List[Tuple[int, int, str]]] = {}
    for label, start, sector_size, filename in _programs(
            preferred or xml_files):
        if label.endswith('_b'):
            continue
        part = label[:-2] if label.endswith('_a') else label
        if targets is not None and part not in targets:
            continue
        # Archive extraction flattens paths, so match on the basename.
        path = os.path.join(directory, os.path.basename(filename))
        if os.path.isfile(path):
            groups.setdefault(part, []).append((start, sector_size, path))

    extracted = []
    for part, pieces in groups.items():
        if logger:
            logger(f"Assembling QFIL partition {part} "
                   f"from {len(pieces)} file(s)...")
        out_path = os.path.join(output_dir, f"{part}.img")
        _assemble(pieces, out_path)
        extracted.append(out_path)
    return extracted
