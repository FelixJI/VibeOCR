namespace VibeOCR.Contracts;

public static class RpcMethods
{
    public const string Handshake = "system.handshake";
    public const string Ping = "system.ping";
    public const string Shutdown = "system.shutdown";
    public const string Cancel = "task.cancel";
    public const string ReleaseMemory = "memory.release";
    public const string Recognize = "ocr.recognize";
    public const string OpenPdf = "pdf.open";
    public const string DecodeQrCode = "qrcode.decode";
    public const string GenerateQrCode = "qrcode.generate";
    public const string SettingsSnapshot = "settings.snapshot";

    public static IReadOnlyList<string> All { get; } =
    [
        Handshake,
        Ping,
        Shutdown,
        Cancel,
        ReleaseMemory,
        Recognize,
        OpenPdf,
        DecodeQrCode,
        GenerateQrCode,
        SettingsSnapshot,
    ];

    internal static void EnsureKnown(string method)
    {
        if (!All.Contains(method, StringComparer.Ordinal))
        {
            throw new ProtocolContractException($"Unknown WorkerHost method: {method}.");
        }
    }
}
