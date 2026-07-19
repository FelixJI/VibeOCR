namespace VibeOCR.Contracts;

public static class RpcMethods
{
    public const string Handshake = "system.handshake";
    public const string Ping = "system.ping";
    public const string Shutdown = "system.shutdown";
    public const string Cancel = "task.cancel";
    public const string ReleaseMemory = "memory.release";
    public const string Recognize = "ocr.recognize";
    public const string RecognizeBatch = "ocr.recognize_batch";
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
    public const string PipelineCacheStatus = "pipeline_cache.status";
    public const string SetPipelineCacheTtl = "pipeline_cache.set_ttl";
    public const string ReleasePipelineCache = "pipeline_cache.release";
    public const string PreloadPipelineCache = "pipeline_cache.preload";
    public const string WarmupPipelineCache = "pipeline_cache.warmup";
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
        RecognizeBatch,
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
        PipelineCacheStatus,
        SetPipelineCacheTtl,
        ReleasePipelineCache,
        PreloadPipelineCache,
        WarmupPipelineCache,
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
