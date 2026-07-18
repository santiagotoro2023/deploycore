using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;

namespace DeployCoreAgent;

/// <summary>
/// Mirrors <c>agent-config.json</c> exactly as
/// <c>backend/app/services/remote_agent_install.ps1</c> writes it (see that
/// script's step 6 and its own comments on why the file starts out
/// plaintext-but-ACL'd). Field names/casing here are load-bearing, not
/// stylistic - the installer (PowerShell) and this agent (C#) only agree on
/// the wire shape of a plain JSON file, not a contract either side compiles
/// against together.
/// </summary>
internal sealed class AgentConfig
{
    [JsonPropertyName("serverUrl")] public string ServerUrl { get; set; } = "";
    [JsonPropertyName("enrollToken")] public string EnrollToken { get; set; } = "";

    // Present only until the very first run re-protects it (see
    // LoadAndProtect below) - null/omitted on every run after that.
    [JsonPropertyName("agentKey")] public string? AgentKeyPlaintext { get; set; }

    // DPAPI-protected (LocalMachine scope), base64 - written by this agent,
    // never by the installer. Present from the second run onward.
    [JsonPropertyName("agentKeyProtected")] public string? AgentKeyProtected { get; set; }

    [JsonPropertyName("turnHost")] public string TurnHost { get; set; } = "";
    [JsonPropertyName("turnPort")] public int TurnPort { get; set; }
    [JsonPropertyName("turnUsername")] public string TurnUsername { get; set; } = "";
    [JsonPropertyName("turnPassword")] public string TurnPassword { get; set; } = "";
    [JsonPropertyName("virtualDisplay")] public bool VirtualDisplay { get; set; }

    /// <summary>
    /// The resolved plaintext agent key for this run - either just
    /// DPAPI-unprotected, or the plaintext value on a genuine first run
    /// before it's been protected yet. Never serialized: the object written
    /// back to disk in <see cref="LoadAndProtect"/> always has
    /// <see cref="AgentKeyPlaintext"/> cleared to null first.
    /// </summary>
    [JsonIgnore]
    public string AgentKey { get; private set; } = "";

    private static readonly JsonSerializerOptions WriteOptions = new()
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = true,
    };

    /// <summary>
    /// Reads agent-config.json and - the actual load-bearing security step,
    /// not decoration - re-protects a plaintext <c>agentKey</c> with DPAPI
    /// the first time it sees one, rewriting the file with
    /// <c>agentKeyProtected</c> instead and dropping the plaintext field
    /// entirely. <c>C:\ProgramData</c> is world-readable by default even
    /// though the installer also tightens this specific file's ACL with
    /// <c>icacls</c> (SYSTEM + Administrators only) right after writing it -
    /// DPAPI (LocalMachine scope, since a service running as SYSTEM has no
    /// interactive user profile to scope a CurrentUser key to) means the
    /// plaintext key can't be recovered even by an account that later gains
    /// read access to this file, only by code running as SYSTEM on this
    /// exact machine.
    /// </summary>
    public static AgentConfig LoadAndProtect(string path, ILogger logger)
    {
        var json = File.ReadAllText(path);
        var config = JsonSerializer.Deserialize<AgentConfig>(json)
                      ?? throw new InvalidDataException($"{path} did not contain a valid agent config.");

        if (!string.IsNullOrEmpty(config.AgentKeyPlaintext))
        {
            logger.LogInformation("First run: protecting agentKey with DPAPI and rewriting {Path}.", path);
            config.AgentKey = config.AgentKeyPlaintext;
            config.AgentKeyProtected = Win32Interop.ProtectToBase64(config.AgentKeyPlaintext);
            config.AgentKeyPlaintext = null;

            var rewritten = JsonSerializer.Serialize(config, WriteOptions);
            // Same path, same file object -> the installer's icacls ACL
            // (SYSTEM + Administrators only) stays attached across this
            // truncate-and-rewrite, since File.WriteAllText opens the
            // existing file rather than deleting and recreating it (Win32
            // CREATE_ALWAYS truncates content, it doesn't reset the
            // security descriptor). Not independently verified against a
            // real NTFS ACL inspection in this environment - if a future
            // audit ever finds this file has drifted back to world-readable
            // permissions, this assumption is the first thing to re-check.
            File.WriteAllText(path, rewritten);
        }
        else if (!string.IsNullOrEmpty(config.AgentKeyProtected))
        {
            config.AgentKey = Win32Interop.UnprotectFromBase64(config.AgentKeyProtected);
        }
        else
        {
            throw new InvalidDataException(
                $"{path} has neither agentKey nor agentKeyProtected - nothing to authenticate the control channel with.");
        }

        return config;
    }
}
