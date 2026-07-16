"""install_app's registry-diff verification heuristic exists to catch
self-relaunching EXE stubs that exit 0 before the real work finishes - a
structural non-issue for MSI, where `msiexec /i ... -Wait` already blocks for
the entire InstallExecuteSequence. Applying it to MSI kind anyway broke a
real "Retry post-install" run: a reinstall of an already-registered product
rewrites its EXISTING Uninstall registry key instead of creating a new one,
so the strict "did a new key appear" diff can never be satisfied and a
successful reinstall gets reported as a 10-minute timeout failure. This
locks in that kind == "msi" skips the diff and trusts msiexec's own exit
code directly, while kind == "exe" keeps the original heuristic."""

from app.winrm.client import WinRMClient


def _captured_script(kind: str) -> str:
    client = WinRMClient("dummy-host", "dummy-user", "dummy-pass")
    captured: dict[str, str] = {}

    def fake_run_as_system_task(script_body: str, task_name: str, max_attempts: int = 240, poll_seconds: int = 10):
        captured["script"] = script_body
        return None

    client._run_as_system_task = fake_run_as_system_task  # type: ignore[method-assign]
    client.install_app("http://example.test/app.bin", "C:\\Windows\\Temp\\app.bin", kind, "/qn")
    return captured["script"]


def test_msi_install_skips_registry_diff_heuristic():
    script = _captured_script("msi")
    assert "$before" not in script
    assert "Get-UninstallEntries" not in script.split("try {")[1]
    assert "$p.ExitCode -eq 3010" in script


def test_exe_install_keeps_registry_diff_heuristic():
    script = _captured_script("exe")
    assert "$before = @(Get-UninstallEntries)" in script
    assert "no new registry Uninstall entry appeared" in script


if __name__ == "__main__":
    test_msi_install_skips_registry_diff_heuristic()
    test_exe_install_keeps_registry_diff_heuristic()
    print("OK")
