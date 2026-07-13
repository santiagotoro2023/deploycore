"""disk_layouts: replace the stored recovery-relocation script with the fixed version

Existing rows got this script pasted in by hand (there's no live template
substitution for post_install_scripts, so a code fix to the canonical
version in setup.py never reaches an already-created row). It's since been
fixed twice - the WinRM command-line-length chunking bug and a
Get-Partition -DiskNumber/-DriveLetter invalid parameter combo - and rather
than ask for a third manual copy-paste, this updates any disk layout whose
post-install scripts include one named exactly like the shipped default,
in place.

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-13

"""
import json

from alembic import op
from sqlalchemy import text

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

SCRIPT_NAME = "Recovery partition relocation (disk layout from hell fix)"

_FIXED_SCRIPT = """
function Info($msg) { Write-Output "[recovery-relocate] $msg" }

function Get-FreeLetter {
    68..90 | ForEach-Object { [char]$_ } | Where-Object { -not (Get-Volume -DriveLetter $_ -ErrorAction SilentlyContinue) } | Select-Object -First 1
}

$recoveryTypeGuid = '{de94bba4-06d1-4d40-a16a-bfd50179d6ac}'

$osPartition = Get-Partition -DriveLetter C -ErrorAction SilentlyContinue
if (-not $osPartition) {
    Info "no C: partition found - unexpected, stopping without changes."
    return
}
if ($osPartition.DiskNumber -ne 0) {
    Info "C: is on disk $($osPartition.DiskNumber), not disk 0 - unexpected, stopping without changes."
    return
}
if ($osPartition.PartitionNumber -eq 3) {
    Info "OS partition is partition 3 - this layout has no recovery_size_mb set, nothing to do."
    return
}

$target = Get-Partition -DiskNumber 0 -PartitionNumber 3
Info "target partition (raw, pre-created by DiskConfiguration): number 3, $([math]::Round($target.Size/1MB)) MB"

$targetLetter = Get-FreeLetter
Set-Partition -DiskNumber 0 -PartitionNumber $target.PartitionNumber -NewDriveLetter $targetLetter
Format-Volume -DriveLetter $targetLetter -FileSystem NTFS -NewFileSystemLabel 'Recovery' -Confirm:$false | Out-Null
Info "formatted target as ${targetLetter}: NTFS"

$source = @(Get-Partition -DiskNumber 0 | Where-Object {
    $_.GptType -eq $recoveryTypeGuid -and $_.PartitionNumber -ne $target.PartitionNumber
})

if ($source.Count -gt 1) {
    Info "found $($source.Count) other recovery-type partitions, expected at most 1 - not safe to proceed automatically, stopping without changes."
    return
}

if ($source.Count -eq 0) {
    $localWinRE = "$env:WINDIR\\System32\\Recovery\\Winre.wim"
    if (-not (Test-Path $localWinRE)) {
        Info "no separate recovery partition and no local Winre.wim found - unexpected state, stopping without changes for manual review."
        return
    }
    Info "WinRE image is stored on C: (no dedicated partition) - relocating that instead of the DISM capture/apply path."
    New-Item -ItemType Directory -Path "${targetLetter}:\\Recovery\\WindowsRE" -Force | Out-Null
    Copy-Item $localWinRE "${targetLetter}:\\Recovery\\WindowsRE\\Winre.wim" -Force
    reagentc /disable
    reagentc /setreimage /path "${targetLetter}:\\Recovery\\WindowsRE"
    if ($LASTEXITCODE -ne 0) { throw "reagentc /setreimage failed with exit code $LASTEXITCODE" }
    reagentc /enable
    if ($LASTEXITCODE -ne 0) { throw "reagentc /enable failed with exit code $LASTEXITCODE" }
    Remove-PartitionAccessPath -DiskNumber 0 -PartitionNumber $target.PartitionNumber -AccessPath "${targetLetter}:\\"
    Set-Partition -DiskNumber 0 -PartitionNumber $target.PartitionNumber -GptType $recoveryTypeGuid
    Set-Partition -DiskNumber 0 -PartitionNumber $target.PartitionNumber -Attributes 0x8000000000000001
    Info "done - WinRE relocated from C: to partition $($target.PartitionNumber)."
    return
}

$src = $source[0]
Info "source partition (Setup's own auto-created recovery): number $($src.PartitionNumber)"

$sourceLetter = Get-FreeLetter
Set-Partition -DiskNumber 0 -PartitionNumber $src.PartitionNumber -NewDriveLetter $sourceLetter
Info "mounted source as ${sourceLetter}:, target as ${targetLetter}:"

$wimPath = "C:\\Windows\\Temp\\deploycore_recovery.wim"
Info "capturing recovery image from ${sourceLetter}:\\ ..."
dism /Capture-Image /ImageFile:$wimPath /CaptureDir:"${sourceLetter}:\\" /Name:"Recovery" /Quiet
if ($LASTEXITCODE -ne 0) { throw "DISM capture failed with exit code $LASTEXITCODE" }

Info "applying recovery image to ${targetLetter}:\\ ..."
dism /Apply-Image /ImageFile:$wimPath /Index:1 /ApplyDir:"${targetLetter}:\\" /Quiet
if ($LASTEXITCODE -ne 0) { throw "DISM apply failed with exit code $LASTEXITCODE" }
Remove-Item $wimPath -Force -ErrorAction SilentlyContinue

Info "repointing reagentc at the new location..."
reagentc /disable
reagentc /setreimage /path "${targetLetter}:\\Recovery\\WindowsRE"
if ($LASTEXITCODE -ne 0) { throw "reagentc /setreimage failed with exit code $LASTEXITCODE" }
reagentc /enable
if ($LASTEXITCODE -ne 0) { throw "reagentc /enable failed with exit code $LASTEXITCODE" }

Info "typing, hiding, and removing the temporary drive letter from the relocated partition..."
Remove-PartitionAccessPath -DiskNumber 0 -PartitionNumber $target.PartitionNumber -AccessPath "${targetLetter}:\\"
Set-Partition -DiskNumber 0 -PartitionNumber $target.PartitionNumber -GptType $recoveryTypeGuid
Set-Partition -DiskNumber 0 -PartitionNumber $target.PartitionNumber -Attributes 0x8000000000000001

Info "deleting Setup's own recovery partition and extending C: into the freed space..."
Remove-PartitionAccessPath -DiskNumber 0 -PartitionNumber $src.PartitionNumber -AccessPath "${sourceLetter}:\\" -ErrorAction SilentlyContinue
Remove-Partition -DiskNumber 0 -PartitionNumber $src.PartitionNumber -Confirm:$false

$maxSize = (Get-PartitionSupportedSize -DiskNumber 0 -PartitionNumber $osPartition.PartitionNumber).SizeMax
Resize-Partition -DiskNumber 0 -PartitionNumber $osPartition.PartitionNumber -Size $maxSize

Info "done - WinRE relocated to partition $($target.PartitionNumber), C: extended to $([math]::Round($maxSize/1GB)) GB."
""".strip()


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, post_install_scripts FROM disk_layouts")).fetchall()
    for row_id, scripts in rows:
        if not scripts:
            continue
        changed = False
        for script in scripts:
            if script.get("name") == SCRIPT_NAME and script.get("script_text") != _FIXED_SCRIPT:
                script["script_text"] = _FIXED_SCRIPT
                changed = True
        if changed:
            conn.execute(
                text("UPDATE disk_layouts SET post_install_scripts = :scripts::jsonb WHERE id = :id"),
                {"scripts": json.dumps(scripts), "id": row_id},
            )


def downgrade() -> None:
    pass
