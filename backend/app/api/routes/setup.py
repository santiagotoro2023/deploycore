from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.disk_layout import DiskLayout
from app.models.setting import Setting, SettingScope
from app.models.user import Role, User
from app.redis import get_redis
from app.schemas.auth import TokenResponse
from app.schemas.setup import SetupRequest, SetupStatus
from app.security.auth import create_access_token, hash_password
from app.security.sessions import create_session
from app.services import audit

router = APIRouter(prefix="/api/setup", tags=["setup"])

# EFI sized per Microsoft's own documented safe minimum for Advanced
# Format 4K-native-sector drives (100 MB is the absolute floor and has
# caused real "BCD: Failed to add system store" Setup failures with no
# headroom to fall back on); recovery partition sized per the "recovery
# mid-disk" technique (a Windows RE tools partition placed before the OS
# volume instead of appended at the end, so expanding the OS volume
# later is not blocked by a trailing recovery partition).
DEFAULT_DISK_LAYOUT_NAME = "Windows Server (Recovery Mid-Disk)"
DEFAULT_DISK_LAYOUT_JSON = {
    "efi_size_mb": 500,
    "msr_size_mb": 128,
    "recovery_size_mb": 1024,
    "os_volume": "remaining",
    "extra_volumes": [],
}
# _disk_configuration.xml.j2 leaves partition 3 (recovery, when
# recovery_size_mb is set) completely raw during Setup - see that
# template's comment for why. So partition 3's identity is now
# deterministic (it's always "whatever CreatePartition put there", not
# something to search for by label/type), and Setup will always end up
# creating its own separate recovery partition too, since ours was never
# typed as WinRE for Setup to find. This replicates the actual working
# fix (https://stastka.ch/knowledge-base/Windows-2022-disk-layout-from-Hell,
# verbatim diskpart/DISM/reagentc recipe translated to native PowerShell
# Storage cmdlets): format our raw partition, capture Setup's own
# recovery image, apply it into ours, repoint reagentc, type+hide ours,
# delete Setup's own, and extend C: into the freed space - the same end
# state the blog's manual diskpart session produces. Real command
# failures intentionally propagate (halting post-install rather than
# leaving a half-relocated recovery partition unnoticed); "can't
# determine what Setup did" cases log and return without changing
# anything rather than guessing.
_RECOVERY_RELOCATE_SCRIPT = """
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

$reagentInfo = (reagentc /info) -join "`n"
if ($reagentInfo -match "partition3\\b") {
    Info "WinRE already relocated to partition 3 - nothing to do (this runs first on every post-install retry too, not just the first attempt)."
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
    Set-Partition -DiskNumber 0 -PartitionNumber $target.PartitionNumber -IsHidden $true -NoDefaultDriveLetter $true
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
Set-Partition -DiskNumber 0 -PartitionNumber $target.PartitionNumber -IsHidden $true -NoDefaultDriveLetter $true

Info "deleting Setup's own recovery partition and extending C: into the freed space..."
Remove-PartitionAccessPath -DiskNumber 0 -PartitionNumber $src.PartitionNumber -AccessPath "${sourceLetter}:\\" -ErrorAction SilentlyContinue
Remove-Partition -DiskNumber 0 -PartitionNumber $src.PartitionNumber -Confirm:$false

$maxSize = (Get-PartitionSupportedSize -DiskNumber 0 -PartitionNumber $osPartition.PartitionNumber).SizeMax
Resize-Partition -DiskNumber 0 -PartitionNumber $osPartition.PartitionNumber -Size $maxSize

Info "done - WinRE relocated to partition $($target.PartitionNumber), C: extended to $([math]::Round($maxSize/1GB)) GB."
""".strip()

DEFAULT_DISK_LAYOUT_POST_INSTALL_SCRIPTS = [
    {"name": "Recovery partition relocation (disk layout from hell fix)", "script_text": _RECOVERY_RELOCATE_SCRIPT}
]


async def _needs_setup(db: AsyncSession) -> bool:
    count = await db.scalar(select(func.count()).select_from(User))
    return count == 0


@router.get("/status", response_model=SetupStatus)
async def setup_status(db: AsyncSession = Depends(get_db)) -> SetupStatus:
    return SetupStatus(needs_setup=await _needs_setup(db))


@router.post("", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def complete_setup(
    body: SetupRequest, db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)
) -> TokenResponse:
    """One-shot instance bootstrap: creates the first (global admin) user
    and the instance name. Refuses once any user already exists, after
    that the instance name is edited from Settings instead."""
    if not await _needs_setup(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "this instance is already set up")

    admin = User(
        username=body.admin_username,
        email=body.admin_email,
        password_hash=hash_password(body.admin_password),
        display_name=body.admin_display_name,
        global_role=Role.ADMIN,
    )
    db.add(admin)
    await db.flush()

    db.add(Setting(scope=SettingScope.GLOBAL, key="instance_name", value=body.instance_name))
    db.add(
        DiskLayout(
            org_id=None,
            name=DEFAULT_DISK_LAYOUT_NAME,
            layout_json=DEFAULT_DISK_LAYOUT_JSON,
            post_install_scripts=DEFAULT_DISK_LAYOUT_POST_INSTALL_SCRIPTS,
        )
    )
    audit.record(
        db,
        action="instance.setup",
        target_type="instance",
        user_id=admin.id,
        detail={"instance_name": body.instance_name},
    )
    await db.commit()

    session_id = await create_session(redis, admin.id)
    return TokenResponse(access_token=create_access_token(admin.id, session_id))
