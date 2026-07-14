using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Win32;
using VibeOCR.Platform.Windows;

namespace VibeOCR.App.Features.Shell;

internal sealed class WindowsHotkeyRegistrar(
    GlobalHotkeyService service,
    string configFile) : IHotkeyRegistrar, IDisposable
{
    private readonly GlobalHotkeyService _service = service;
    private readonly string _configFile = configFile;
    private IDisposable? _registration;
    private int _nextId;

    public bool Register(string hotkey, out string? conflict)
    {
        try
        {
            (HotkeyModifiers modifiers, uint virtualKey) = Parse(hotkey);
            IDisposable next = _service.Register(
                Interlocked.Increment(ref _nextId),
                modifiers | HotkeyModifiers.NoRepeat,
                virtualKey);
            _registration?.Dispose();
            _registration = next;
            Persist(hotkey);
            conflict = null;
            return true;
        }
        catch (Exception error) when (
            error is ArgumentException or HotkeyRegistrationException or InvalidOperationException)
        {
            conflict = error.Message;
            return false;
        }
    }

    public void Unregister()
    {
        Interlocked.Exchange(ref _registration, null)?.Dispose();
    }

    public void Dispose()
    {
        Unregister();
        _service.Dispose();
    }

    private void Persist(string hotkey)
    {
        JsonObject root = File.Exists(_configFile)
            ? JsonNode.Parse(File.ReadAllText(_configFile))?.AsObject() ?? []
            : [];
        JsonObject hotkeys = root["hotkeys"] as JsonObject ?? [];
        hotkeys["global_screenshot"] = hotkey;
        root["hotkeys"] = hotkeys;
        Directory.CreateDirectory(Path.GetDirectoryName(_configFile)!);
        string temporary = _configFile + ".tmp";
        File.WriteAllText(temporary, root.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        File.Move(temporary, _configFile, overwrite: true);
    }

    private static (HotkeyModifiers Modifiers, uint VirtualKey) Parse(string hotkey)
    {
        string[] tokens = hotkey.Split('+', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
        if (tokens.Length < 2)
        {
            throw new ArgumentException("快捷键必须包含修饰键和主键。", nameof(hotkey));
        }

        HotkeyModifiers modifiers = HotkeyModifiers.None;
        foreach (string token in tokens[..^1])
        {
            modifiers |= token.ToUpperInvariant() switch
            {
                "CTRL" or "CONTROL" => HotkeyModifiers.Control,
                "ALT" => HotkeyModifiers.Alt,
                "SHIFT" => HotkeyModifiers.Shift,
                "WIN" or "WINDOWS" => HotkeyModifiers.Windows,
                _ => throw new ArgumentException($"不支持的快捷键修饰符：{token}", nameof(hotkey)),
            };
        }

        string key = tokens[^1].ToUpperInvariant();
        uint virtualKey = key.Length == 1 && char.IsLetterOrDigit(key[0])
            ? key[0]
            : key.StartsWith('F') && int.TryParse(key[1..], out int number) && number is >= 1 and <= 24
                ? (uint)(0x70 + number - 1)
                : throw new ArgumentException($"不支持的快捷键主键：{key}", nameof(hotkey));
        return (modifiers, virtualKey);
    }
}

internal sealed class WindowsStartupRegistrar(string bootstrapperPath) : IStartupRegistrar
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "VibeOCR";
    private readonly string _command = $"\"{bootstrapperPath}\" --profile production";

    public bool SetEnabled(bool enabled)
    {
        try
        {
            using RegistryKey key = Registry.CurrentUser.CreateSubKey(RunKey, writable: true);
            if (enabled)
            {
                key.SetValue(ValueName, _command, RegistryValueKind.String);
            }
            else
            {
                key.DeleteValue(ValueName, throwOnMissingValue: false);
            }
            return true;
        }
        catch (Exception error) when (error is UnauthorizedAccessException or IOException)
        {
            return false;
        }
    }
}
