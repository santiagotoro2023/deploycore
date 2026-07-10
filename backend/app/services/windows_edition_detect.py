import logging
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

# A Windows Server install ISO's install.wim (or, less commonly for
# volume-license media, install.esd) holds several editions in one file,
# e.g. Server Core and Desktop Experience for both Standard and
# Datacenter, each addressed by a numeric /IMAGE/INDEX in the answer
# file. autounattend_base.xml.j2 used to hardcode index 1, which on the
# standard Microsoft image ordering is Server Core, not what most people
# mean by "install Windows Server" by default. Detecting the real list
# once at upload time (rather than guessing a fixed index that varies by
# ISO) lets template creation offer an actual dropdown instead.
#
# Uses 7z (p7zip-full), not xorriso, deliberately: Microsoft's own ISO
# builder lays out Windows Setup media as a UDF+ISO9660+Joliet hybrid
# specifically so install.wim can exceed the 4 GiB plain-ISO9660 file size
# limit (a multi-edition Server WIM routinely does). xorriso 1.5.4 can
# list such a file, but silently truncates it on -osirrox extraction
# (confirmed against a real >4 GiB UDF test image, exit 0, "1 files
# restored", extracted file a fraction of the real size, no error at
# all), which corrupts install.wim before wimlib-imagex ever sees it. 7z
# reads the UDF tree directly and both lists and extracts the full,
# correct byte count in the same scenario.
_INSTALL_NAMES = {"install.wim", "install.esd"}


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {(result.stderr or result.stdout)[-2000:]}")
    return result.stdout


def _find_path(iso_path: Path) -> str | None:
    # `-ba` (bare) drops 7z's header/summary lines, leaving one line per
    # entry: "<date> <time> <attr> <size> <compressed>  <name>", with
    # <size>/<compressed> blank (and so absent as tokens) for directories.
    # split(None, 5) is exactly right either way: it stops splitting once
    # it hits the name, which is the only field that can itself contain
    # whitespace.
    stdout = _run(["7z", "l", "-ba", str(iso_path)])
    for line in stdout.splitlines():
        parts = line.split(None, 5)
        if not parts:
            continue
        name = parts[-1]
        if name.rsplit("/", 1)[-1].lower() in _INSTALL_NAMES:
            return name
    return None


def detect_editions(iso_path: Path) -> list[dict]:
    """Best-effort: returns [] on anything unexpected (no install.wim/.esd
    found, 7z/wimlib-imagex missing or failing, XML doesn't parse, ...)
    rather than raising, an ISO that isn't laid out the way Microsoft's own
    Windows Server media is just doesn't get a dropdown, the template form
    falls back to a plain index field instead of losing the upload."""
    try:
        wim_path = _find_path(iso_path)
        if wim_path is None:
            logger.info("windows_edition_detect: no install.wim/install.esd found in %s", iso_path)
            return []

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # `x` (not `e`) so the extracted file lands at tmp_dir/wim_path,
            # preserving whatever subdirectory 7z listed it under (normally
            # sources/); read-only against the ISO either way, unrelated to
            # iso_remaster.py's separate in-place boot-prompt patch.
            _run(["7z", "x", f"-o{tmp_dir}", str(iso_path), wim_path, "-y"])
            local_wim = tmp_dir / wim_path

            xml_path = tmp_dir / "images.xml"
            _run(["wimlib-imagex", "info", str(local_wim), "--extract-xml", str(xml_path)])
            # WIM's embedded metadata XML is UTF-16 (with a BOM), not UTF-8.
            root = ET.fromstring(xml_path.read_bytes().decode("utf-16"))

        editions = []
        for image in root.findall("IMAGE"):
            index = image.get("INDEX")
            if index is None:
                continue
            name = image.findtext("NAME") or ""
            description = image.findtext("DESCRIPTION") or ""
            # Microsoft's own FLAGS value is the one machine-readable signal
            # for which edition this is (e.g. "ServerStandard" vs.
            # "ServerStandardCore", "ServerDatacenter" vs.
            # "ServerDatacenterCore"): every Core (no GUI) SKU's flag ends in
            # "Core", every Desktop Experience (has GUI) one doesn't. NAME/
            # DESCRIPTION usually spell "(Desktop Experience)" out too, but
            # aren't guaranteed to (older/localized media), FLAGS always is.
            flags = image.findtext("FLAGS") or ""
            has_gui = "core" not in flags.lower() if flags else "core" not in f"{name} {description}".lower()
            editions.append({
                "index": int(index),
                "name": name,
                "description": description,
                "has_gui": has_gui,
            })
        return editions
    except Exception:  # noqa: BLE001 - best-effort, see docstring
        logger.exception("windows_edition_detect: failed to detect editions in %s", iso_path)
        return []
