import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Windows install media boots via an El Torito boot catalog with (among
# others) a UEFI "no emulation" boot image at efi/microsoft/boot/efisys.bin.
# That image is built by Microsoft to print "Press any key to boot from CD
# or DVD..." and wait indefinitely for a keystroke, there is no unattend.xml
# setting that suppresses it, which is why worker/tasks/provision.py has had
# to fake keypresses over the ESXi console to get past it.
#
# Every stock Windows install ISO also ships a second, silent UEFI boot
# image at efi/microsoft/boot/efisys_noprompt.bin, built by Microsoft for
# exactly this scenario (it's what WDS/MDT-style unattended deployment
# tooling relies on). Swapping it in for efisys.bin before the ISO is ever
# uploaded to a datastore removes the prompt permanently for every future
# deployment built from it, no interaction and no synthetic keypress
# required. This only patches the UEFI boot image; our VMs are provisioned
# EFI-only (see hypervisors/defaults.py), so that's sufficient.
_EFISYS_GLOB = "[eE][fF][iI][sS][yY][sS].[bB][iI][nN]"
_EFISYS_NOPROMPT_GLOB = "[eE][fF][iI][sS][yY][sS]_[nN][oO][pP][rR][oO][mM][pP][tT].[bB][iI][nN]"


class IsoRemasterError(RuntimeError):
    pass


def _run_xorriso(args: list[str]) -> str:
    result = subprocess.run(["xorriso", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise IsoRemasterError(f"xorriso {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout


def _find_path(iso_path: Path, name_glob: str) -> str | None:
    """Returns the exact in-ISO path (case exactly as stored, which varies
    by whatever tool built the ISO) of the first file whose name matches
    name_glob case-insensitively, or None if nothing matches. xorriso 1.5.4
    (Debian bookworm) has no -iname, hence the bracket-glob."""
    stdout = _run_xorriso(["-indev", str(iso_path), "-find", "/", "-name", name_glob])
    for line in stdout.splitlines():
        line = line.strip().strip("'")
        if line.startswith("/"):
            return line
    return None


def remove_boot_prompt(iso_path: Path) -> bool:
    """Rewrites the ISO at iso_path in place so its UEFI boot image no
    longer shows the "press any key" prompt. Returns True if the ISO was
    modified, False if it doesn't look like Windows install media (no
    efisys.bin/efisys_noprompt.bin pair found) and was left untouched.

    Raises IsoRemasterError if xorriso fails partway through; callers
    should treat that as non-fatal and keep serving the original ISO, since
    the keypress fallback in provision.py still covers an unmodified image.
    """
    efisys_path = _find_path(iso_path, _EFISYS_GLOB)
    noprompt_path = _find_path(iso_path, _EFISYS_NOPROMPT_GLOB)
    if efisys_path is None or noprompt_path is None:
        logger.info("iso_remaster: no efisys.bin/efisys_noprompt.bin pair in %s, leaving it as-is", iso_path)
        return False

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        local_noprompt = tmp_dir / "efisys_noprompt.bin"
        _run_xorriso(["-osirrox", "on", "-indev", str(iso_path), "-extract", noprompt_path, str(local_noprompt)])

        rebuilt = tmp_dir / "rebuilt.iso"
        # Order matters: replaying the boot catalog before mapping the new
        # file content works around a known xorriso 1.5.4/1.5.6 bug where
        # replaying *after* a -map of the boot image itself fails with
        # "Cannot enable EL Torito boot image ... not a data file" (fixed
        # upstream in 1.5.7, not yet in Debian bookworm).
        _run_xorriso([
            "-indev", str(iso_path),
            "-outdev", str(rebuilt),
            "-boot_image", "any", "replay",
            "-map", str(local_noprompt), efisys_path,
            "-compliance", "no_emul_toc",
            "-commit",
        ])
        shutil.move(str(rebuilt), str(iso_path))
    return True
