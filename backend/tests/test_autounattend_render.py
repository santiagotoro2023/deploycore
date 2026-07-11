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
        custom_admin_enabled=False,
        local_admin_username="Administrator",
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


def _make_deployment(**overrides) -> Deployment:
    defaults = dict(
        org_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        hypervisor_host_id=uuid.uuid4(),
        hostname="TESTHOST01",
        ip_mode=IpMode.DHCP,
        callback_token="test-token-123",
        created_by_user_id=uuid.uuid4(),
    )
    defaults.update(overrides)
    return Deployment(**defaults)


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


def test_oobe_stays_at_the_last_confirmed_working_set():
    """ProtectYourPC/NetworkLocation/HideOEMRegistrationScreen have been
    added and reverted twice now. First revert: alongside the old
    declarative <AutoLogon> element, on the theory either might be
    involved in a total Setup failure - inconclusive, since removing them
    alone (AutoLogon still present) didn't fix that failure, only removing
    AutoLogon did, but that also never actually tested this OOBE set
    without AutoLogon in the picture. Second attempt: reintroduced
    alongside the new specialize-pass reg.exe autologon (a real deployment
    had never tested this specific combination) - Setup failed again with
    the exact same WINDEPLOY 0x80220005 signature as the AutoLogon-era
    failures, and this time setupact.log showed specialize (reg.exe
    commands included) completing cleanly before failing at the
    Pre-OOBE -> OOBE transition these settings directly govern, a much
    more direct implication than already-finished specialize-pass work.
    Reverted again to isolate whether they're the actual cause. Locking in
    the six-element set last confirmed to actually complete a real
    install, so a future change doesn't silently reintroduce the other
    three without a real reason."""
    root = etree.fromstring(render_autounattend(_make_deployment(), _make_template(), _basic_disk_layout()).encode())

    oobe = root.xpath("//u:component[@name='Microsoft-Windows-Shell-Setup']/u:OOBE", namespaces=NS)[0]
    assert [c.tag.split("}")[-1] for c in oobe] == [
        "HideEULAPage",
        "HideLocalAccountScreen",
        "HideOnlineAccountScreens",
        "HideWirelessSetupInOOBE",
        "SkipMachineOOBE",
        "SkipUserOOBE",
    ]


def test_no_autologon_mechanism_at_all():
    """Two different auto-logon mechanisms have each broken Setup outright
    on real hardware: the declarative <AutoLogon> element (oobeSystem
    pass), and a specialize-pass Microsoft-Windows-Deployment/
    RunSynchronousCommand writing the same registry values via reg.exe
    (tried after the element failed, on the theory a different Setup code
    path would avoid whatever it was hitting - it didn't, same WINDEPLOY
    0x80220005 failure, confirmed with OOBE settings already reverted so
    the RunSynchronousCommand component was the only variable left). The
    one deployment that's actually completed had neither. Locked in as
    fully absent - no <AutoLogon>, no Microsoft-Windows-Deployment
    component at all - until a mechanism is found that doesn't hit
    whatever both of these did. FirstLogonCommands only run once a human
    physically logs in at the console until then."""
    template = _make_template()
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    assert root.xpath("//u:AutoLogon", namespaces=NS) == []
    assert root.xpath("//u:component[@name='Microsoft-Windows-Deployment']", namespaces=NS) == []

    commands = root.xpath("//u:FirstLogonCommands/u:SynchronousCommand", namespaces=NS)
    command_lines = " ".join(c.xpath("string(u:CommandLine)", namespaces=NS) for c in commands)
    assert "DefaultPassword" not in command_lines and "AutoAdminLogon" not in command_lines


def test_custom_admin_disabled_by_default_keeps_builtin_administrator():
    """Off by default: no LocalAccounts entry, the built-in Administrator
    keeps its password and is never touched by FirstLogonCommands, and
    only the two baseline commands (enable WinRM, callback) render."""
    template = _make_template()
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    assert root.xpath("//u:UserAccounts/u:LocalAccounts", namespaces=NS) == []
    assert root.xpath("string(//u:AdministratorPassword/u:Value)", namespaces=NS) == "P@ssw0rd1!"

    commands = root.xpath("//u:FirstLogonCommands/u:SynchronousCommand", namespaces=NS)
    command_lines = " ".join(c.xpath("string(u:CommandLine)", namespaces=NS) for c in commands)
    assert len(commands) == 2
    assert "LocalAccountTokenFilterPolicy" not in command_lines
    assert "Disable-LocalUser" not in command_lines


def test_static_ip_configured_declaratively_not_over_dhcp():
    """The static IP has to be live before Windows Setup even finishes,
    set declaratively in the specialize pass, not reconfigured over WinRM
    afterward (that used to require a DHCP-assigned address to connect to
    in the first place, which doesn't exist on a DHCP-less network)."""
    deployment = _make_deployment(
        ip_mode=IpMode.STATIC,
        static_ip="192.168.10.50",
        static_netmask="255.255.255.0",
        static_gateway="192.168.10.1",
        static_dns=["192.168.10.2", "8.8.8.8"],
    )
    root = etree.fromstring(render_autounattend(deployment, _make_template(), _basic_disk_layout()).encode())

    tcpip = root.xpath("//u:component[@name='Microsoft-Windows-TCPIP']", namespaces=NS)[0]
    assert tcpip.xpath("string(.//u:Identifier)", namespaces=NS) == "Ethernet"
    assert tcpip.xpath("string(.//u:DhcpEnabled)", namespaces=NS) == "false"
    assert tcpip.xpath("string(.//u:IpAddress)", namespaces=NS) == "192.168.10.50/24"
    assert tcpip.xpath("string(.//u:NextHopAddress)", namespaces=NS) == "192.168.10.1"

    dns_addresses = root.xpath(
        "//u:component[@name='Microsoft-Windows-DNS-Client']//u:IpAddress/text()", namespaces=NS
    )
    assert dns_addresses == ["192.168.10.2", "8.8.8.8"]


def test_dhcp_deployment_has_no_static_network_component():
    root = etree.fromstring(
        render_autounattend(_make_deployment(), _make_template(), _basic_disk_layout()).encode()
    )
    assert root.xpath("//u:component[@name='Microsoft-Windows-TCPIP']", namespaces=NS) == []
    assert root.xpath("//u:component[@name='Microsoft-Windows-DNS-Client']", namespaces=NS) == []


def test_local_accounts_creates_custom_admin_and_still_sets_builtin_password():
    """With the toggle on: the built-in Administrator gets a password too
    (Setup needs one to exist momentarily) but _first_logon_commands.xml.j2
    disables it within seconds of first boot; the LocalAccounts-created
    account is the one meant to survive."""
    template = _make_template(custom_admin_enabled=True, local_admin_username="svcwinadmin")
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    local_account = root.xpath("//u:UserAccounts/u:LocalAccounts/u:LocalAccount", namespaces=NS)
    assert len(local_account) == 1
    assert local_account[0].xpath("string(u:Name)", namespaces=NS) == "svcwinadmin"
    assert local_account[0].xpath("string(u:Group)", namespaces=NS) == "Administrators"
    assert local_account[0].xpath("string(u:Password/u:Value)", namespaces=NS) == "P@ssw0rd1!"

    assert root.xpath("string(//u:AdministratorPassword/u:Value)", namespaces=NS) == "P@ssw0rd1!"

    commands = root.xpath("//u:FirstLogonCommands/u:SynchronousCommand", namespaces=NS)
    command_lines = " ".join(c.xpath("string(u:CommandLine)", namespaces=NS) for c in commands)
    assert len(commands) == 4
    assert "LocalAccountTokenFilterPolicy" in command_lines
    assert "Disable-LocalUser -Name 'Administrator'" in command_lines


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


def test_installed_os_locale_set_in_specialize_pass():
    """Microsoft-Windows-International-Core-WinPE (windowsPE pass) only
    covers Setup's own UI while it runs; without the separate
    Microsoft-Windows-International-Core component in specialize/
    oobeSystem, the *installed* OS silently keeps whatever locale/keyboard
    the base image defaults to."""
    template = _make_template(locale="de-CH", keyboard_layout="de-CH")
    root = etree.fromstring(render_autounattend(_make_deployment(), template, _basic_disk_layout()).encode())

    installed_intl = root.xpath("//u:component[@name='Microsoft-Windows-International-Core']", namespaces=NS)
    assert len(installed_intl) == 1
    assert installed_intl[0].xpath("string(u:InputLocale)", namespaces=NS) == "0807:00000807"
    assert installed_intl[0].xpath("string(u:SystemLocale)", namespaces=NS) == "de-CH"
    assert installed_intl[0].xpath("string(u:UserLocale)", namespaces=NS) == "de-CH"


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
