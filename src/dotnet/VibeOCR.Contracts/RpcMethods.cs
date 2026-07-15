namespace VibeOCR.Contracts;

public static class RpcMethods
{
    public const string Handshake = "system.handshake";
    public const string Ping = "system.ping";
    public const string Shutdown = "system.shutdown";
    public const string Cancel = "task.cancel";
    public const string ReleaseMemory = "memory.release";
    public const string Recognize = "ocr.recognize";
    public const string ExportOcr = "ocr.export";
    public const string OpenPdf = "pdf.open";
    public const string ClosePdf = "pdf.close";
    public const string PdfCommand = "pdf.command";
    public const string RenderPdfPage = "pdf.render_page";
    public const string RotatePdf = "pdf.rotate";
    public const string DeletePdfPages = "pdf.delete_pages";
    public const string AddPdfTextLayer = "pdf.add_text_layer";
    public const string DeletePdfTextLayers = "pdf.delete_text_layers";
    public const string SavePdf = "pdf.save";
    public const string StartPdfOcr = "pdf.start_ocr";
    public const string DecodeQrCode = "qrcode.decode";
    public const string GenerateQrCode = "qrcode.generate";
    public const string GenerateQrCodeSvg = "qrcode.generate_svg";
    public const string SettingsSnapshot = "settings.snapshot";
    public const string SwitchBackend = "settings.switch_backend";
    public const string InstallDependency = "settings.install_dependency";

    public static IReadOnlyList<string> All { get; } =
    [
        Handshake,
        Ping,
        Shutdown,
        Cancel,
        ReleaseMemory,
        Recognize,
        ExportOcr,
        OpenPdf,
        ClosePdf,
        PdfCommand,
        RenderPdfPage,
        RotatePdf,
        DeletePdfPages,
        AddPdfTextLayer,
        DeletePdfTextLayers,
        SavePdf,
        StartPdfOcr,
        DecodeQrCode,
        GenerateQrCode,
        GenerateQrCodeSvg,
        SettingsSnapshot,
        SwitchBackend,
        InstallDependency,
    ];

    internal static void EnsureKnown(string method)
    {
        if (!All.Contains(method, StringComparer.Ordinal))
        {
            throw new ProtocolContractException($"Unknown WorkerHost method: {method}.");
        }
    }
}
