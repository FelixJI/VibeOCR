// HttpClient-based PDF session client for the v2 supervisor's /v2/pdf/sessions/* routes.
using System.Text.Json;
using VibeOCR.Runtime.Client;
using VibeOCR.Runtime.Contracts.Generated;

namespace VibeOCR.Platform.Inference;

public sealed class PdfSessionHttpClient : IPdfSessionClient
{
    private readonly RuntimeHttpClient _runtime;

    public PdfSessionHttpClient(Uri baseUrl, string sessionToken, HttpMessageHandler? handler = null)
    {
        _runtime = new RuntimeHttpClient(baseUrl, sessionToken, handler);
    }

    public async Task<PdfSessionOpenResult> OpenAsync(string path, string? password, CancellationToken ct)
    {
        using StringContent content = _runtime.CreateJsonContent(new { path, password });
        using HttpResponseMessage resp = await _runtime.PostAsync(
            RuntimeOperationPaths.OpenPdfSession, content, ct);
        await _runtime.EnsureSuccessAsync(resp, ct);
        using JsonDocument doc = await _runtime.ReadJsonDocumentAsync(resp, ct);
        JsonElement root = doc.RootElement;
        return new PdfSessionOpenResult(
            root.GetProperty("session_id").GetString()!,
            root.GetProperty("page_count").GetInt32(),
            root.GetProperty("file_path").GetString()!);
    }

    public async Task<byte[]> RenderAsync(string sessionId, int page, int size, CancellationToken ct)
    {
        using HttpResponseMessage resp = await _runtime.GetAsync(
            $"{BindSessionPath(RuntimeOperationPaths.RenderPdfPage, sessionId)}?page={page}&size={size}", ct);
        await _runtime.EnsureSuccessAsync(resp, ct);
        return await _runtime.ReadBinaryAsync(resp, "image/png", ct);
    }

    public async Task<PdfMutateResult> RotateAsync(string sessionId, int[] pages, int angle, CancellationToken ct)
    {
        using StringContent content = _runtime.CreateJsonContent(new { pages, angle });
        using HttpResponseMessage resp = await _runtime.PostAsync(
            BindSessionPath(RuntimeOperationPaths.RotatePdfPages, sessionId), content, ct);
        await _runtime.EnsureSuccessAsync(resp, ct);
        using JsonDocument doc = await _runtime.ReadJsonDocumentAsync(resp, ct);
        return new PdfMutateResult(doc.RootElement.GetProperty("page_count").GetInt32());
    }

    public async Task<PdfMutateResult> DeletePagesAsync(string sessionId, int[] pages, CancellationToken ct)
    {
        using StringContent content = _runtime.CreateJsonContent(new { pages });
        using HttpResponseMessage resp = await _runtime.PostAsync(
            BindSessionPath(RuntimeOperationPaths.DeletePdfPages, sessionId), content, ct);
        await _runtime.EnsureSuccessAsync(resp, ct);
        using JsonDocument doc = await _runtime.ReadJsonDocumentAsync(resp, ct);
        return new PdfMutateResult(doc.RootElement.GetProperty("page_count").GetInt32());
    }

    public async Task<string> SaveAsync(string sessionId, string outputPath, CancellationToken ct)
    {
        using StringContent content = _runtime.CreateJsonContent(
            new { output_path = outputPath });
        using HttpResponseMessage resp = await _runtime.PostAsync(
            BindSessionPath(RuntimeOperationPaths.SavePdfSession, sessionId), content, ct);
        await _runtime.EnsureSuccessAsync(resp, ct);
        using JsonDocument doc = await _runtime.ReadJsonDocumentAsync(resp, ct);
        return doc.RootElement.GetProperty("saved_path").GetString()!;
    }

    public async Task CloseAsync(string sessionId, CancellationToken ct)
    {
        using HttpResponseMessage resp = await _runtime.PostAsync(
            BindSessionPath(RuntimeOperationPaths.ClosePdfSession, sessionId), content: null, ct);
        await _runtime.EnsureSuccessAsync(resp, ct);
    }

    public ValueTask DisposeAsync()
    {
        return _runtime.DisposeAsync();
    }

    private static string BindSessionPath(string template, string sessionId) =>
        template.Replace(
            "{session_id}", Uri.EscapeDataString(sessionId), StringComparison.Ordinal);
}
