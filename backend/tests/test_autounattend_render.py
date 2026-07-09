import uuid

from lxml import etree

from app.models.deployment import Deployment, IpMode
from app.models.disk_layout import DiskLayout
from app.models.template import DeploymentTemplate, DomainJoinTiming
from app.services.template_render import render_autounattend

NS = {"u": "urn:schemas-microsoft-com:unattend"}


def _make_template(**overrides) -> DeploymentTemplate:
    defaults = dict(
        name="test-template",
        iso_asset_id=uuid.uuid4(),
        disk_layout_id=uuid.uuid4(),
        cpu_count=2,
        ram_mb=4096,
        disk_size_gb=80,
        network_name="VM Network",
        domain_join_enabled=False,
        domain_join_timing=DomainJoinTiming.ANSWER_FILE,
        windows_features=[],
        post_install_scripts=[],
    )
    defaults.update(overrides)
    template = DeploymentTemplate(**defaults)
    template.local_admin_password = "P@ssw0rd1!"
    if defaults.get("domain_join_enabled"):
        template.domain_join_credential = "DomainJoinPass1!"
    return template


def _make_deployment() -> Deployment:
    return Deployment(
        org_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        hypervisor_host_id=uuid.uuid4(),
        hostname="TESTHOST01",
        ip_mode=IpMode.DHCP,
        callback_token="test-token-123",
        created_by_user_id=uuid.uuid4(),
    )


def _basic_disk_layout(**layout_overrides) -> DiskLayout:
    layout_json = {"efi_size_mb": 500, "msr_size_mb": 128, "os_volume": "remaining", "extra_volumes": []}
    layout_json.update(layout_overrides)
    return DiskLayout(name="basic", layout_json=layout_json)


def test_domain_join_present_when_enabled_at_answer_file_time():
    template = _make_template(
        domain_join_enabled=True,
        domain_fqdn="corp.example.com",
        domain_join_account="svc-join",
        domain_target_ou="OU=Servers,DC=corp,DC=example,DC=com",
    )
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    join = root.xpath("//u:component[@name='Microsoft-Windows-UnattendedJoin']", namespaces=NS)
    assert len(join) == 1
    assert join[0].xpath("string(.//u:JoinDomain)", namespaces=NS) == "corp.example.com"
    assert join[0].xpath("string(.//u:MachineObjectOU)", namespaces=NS) == "OU=Servers,DC=corp,DC=example,DC=com"


def test_domain_join_absent_when_disabled():
    template = _make_template(domain_join_enabled=False)
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    assert root.xpath("//u:component[@name='Microsoft-Windows-UnattendedJoin']", namespaces=NS) == []


def test_domain_join_absent_when_deferred_to_post_install():
    template = _make_template(
        domain_join_enabled=True,
        domain_join_timing=DomainJoinTiming.POST_INSTALL,
        domain_fqdn="corp.example.com",
        domain_join_account="svc-join",
    )
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    # deferred to post-install WinRM join, so the answer file must still
    # produce a plain workgroup machine
    assert root.xpath("//u:component[@name='Microsoft-Windows-UnattendedJoin']", namespaces=NS) == []


def test_disk_layout_remaining_os_volume():
    root = etree.fromstring(
        render_autounattend(_make_deployment(), _make_template(), _basic_disk_layout()).encode()
    )
    partitions = root.xpath("//u:CreatePartition", namespaces=NS)
    assert len(partitions) == 3
    assert partitions[2].xpath("string(.//u:Extend)", namespaces=NS) == "true"


def test_disk_layout_fixed_os_volume_and_extra_volumes():
    disk_layout = _basic_disk_layout(
        os_volume={"size_mb": 102400},
        extra_volumes=[{"label": "Data", "drive_letter": "D", "size_mb": 51200}],
    )
    root = etree.fromstring(render_autounattend(_make_deployment(), _make_template(), disk_layout).encode())

    create_partitions = root.xpath("//u:CreatePartition", namespaces=NS)
    assert len(create_partitions) == 4
    assert create_partitions[2].xpath("string(.//u:Size)", namespaces=NS) == "102400"

    modify_partitions = root.xpath("//u:ModifyPartition", namespaces=NS)
    assert len(modify_partitions) == 4
    assert modify_partitions[3].xpath("string(.//u:Label)", namespaces=NS) == "Data"
    assert modify_partitions[3].xpath("string(.//u:Letter)", namespaces=NS) == "D"


def test_disk_layout_with_recovery_partition_mid_disk():
    disk_layout = _basic_disk_layout(recovery_size_mb=1000)
    root = etree.fromstring(render_autounattend(_make_deployment(), _make_template(), disk_layout).encode())

    create_partitions = root.xpath("//u:CreatePartition", namespaces=NS)
    assert len(create_partitions) == 4
    assert create_partitions[2].xpath("string(.//u:Size)", namespaces=NS) == "1000"

    modify_partitions = root.xpath("//u:ModifyPartition", namespaces=NS)
    assert len(modify_partitions) == 4
    recovery_partition = modify_partitions[2]
    assert recovery_partition.xpath("string(.//u:Label)", namespaces=NS) == "Windows RE tools"
    assert recovery_partition.xpath("string(.//u:TypeID)", namespaces=NS) == "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
    assert recovery_partition.xpath(".//u:Letter", namespaces=NS) == []

    install_to = root.xpath("//u:InstallTo/u:PartitionID", namespaces=NS)
    assert install_to[0].text == "4"


def test_callback_url_and_token_in_first_logon_commands():
    deployment = _make_deployment()
    root = etree.fromstring(
        render_autounattend(deployment, _make_template(), _basic_disk_layout()).encode()
    )
    commands = root.xpath("//u:FirstLogonCommands", namespaces=NS)
    assert len(commands) == 1
    joined = etree.tostring(commands[0]).decode()
    assert f"/api/callback/{deployment.callback_token}" in joined


def test_special_characters_in_password_are_escaped_not_corrupted():
    """A field containing &, <, or > must not break the XML: Setup silently
    ignores an unparseable answer file and falls back to interactive
    install with no visible error, exactly the failure mode this guards
    against (see template_render.py's autoescape comment)."""
    template = _make_template(
        domain_join_enabled=True,
        domain_fqdn="corp.example.com",
        domain_join_account="svc-join",
    )
    template.local_admin_password = "P&ssw0rd<1>!"
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    password = root.xpath("string(//u:AdministratorPassword/u:Value)", namespaces=NS)
    assert password == "P&ssw0rd<1>!"


def test_ui_language_has_a_fallback():
    """Without UILanguageFallback, Setup has nothing to fall back to if
    the requested locale isn't a valid Setup UI language on the specific
    install media, and shows the interactive language/time/keyboard
    picker instead of guessing, exactly the screen this exists to
    suppress (every known-working real-world answer file sets it)."""
    template = _make_template(locale="de-CH", keyboard_layout="de-CH")
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    winpe_intl = root.xpath(
        "//u:component[@name='Microsoft-Windows-International-Core-WinPE']", namespaces=NS
    )[0]
    assert winpe_intl.xpath("string(u:UILanguageFallback)", namespaces=NS) == "de-CH"
    assert winpe_intl.xpath("string(u:SetupUILanguage/u:UILanguage)", namespaces=NS) == "de-CH"


def test_input_locale_resolves_bare_locale_tag_to_named_keyboard():
    """A bare "de-CH" InputLocale picks *a* default keyboard for that
    locale, not necessarily the Swiss German one, Setup silently landed on
    the plain German layout instead. The documented fix is the explicit
    "LCID:KLID" pair, which for a locale's namesake keyboard is always its
    LCID hex with "0000" prefixed (e.g. "0807:00000807" for de-CH, per
    Microsoft's own sample answer files and the MS-LCID reference)."""
    template = _make_template(locale="de-CH", keyboard_layout="de-CH")
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    winpe_intl = root.xpath(
        "//u:component[@name='Microsoft-Windows-International-Core-WinPE']", namespaces=NS
    )[0]
    assert winpe_intl.xpath("string(u:InputLocale)", namespaces=NS) == "0807:00000807"


def test_input_locale_passes_through_explicit_lcid_klid_pair():
    """A value already in "LCID:KLID" form covers a locale outside our
    known-name table, or a non-default keyboard on a supported one, and
    must not be second-guessed."""
    template = _make_template(keyboard_layout="0409:00020409")
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    winpe_intl = root.xpath(
        "//u:component[@name='Microsoft-Windows-International-Core-WinPE']", namespaces=NS
    )[0]
    assert winpe_intl.xpath("string(u:InputLocale)", namespaces=NS) == "0409:00020409"
