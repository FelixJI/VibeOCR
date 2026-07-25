// HttpClient-based PDF session client for the v2 supervisor's /v2/pdf/sessions/* routes.
using System.Net.Http.Headers;
using System.Text.Json;

namespace VibeOCR.Platform.Inference;

public sealed class PdfSessionHttpClient : IPdfSessionClient
{
    private readonly HttpClient _http;
    private readonly bool _ownsHttp;

    public PdfSessionHttpClient(Uri baseUrl, string sessionToken, HttpMessageHandler? handler = null)
    {
        _ownsHttp = handler is not null;
        _http = handler is null ? new HttpClient() : new HttpClient(handler);
        _http.BaseAddress = baseUrl;
        _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", sessionToken);
    }

    public async Task<PdfSessionOpenResult> OpenAsync(string path, string? password, CancellationToken ct)
    {
        using StringContent content = new(
            JsonSerializer.Serialize(new { path, password }),
            System.Text.Encoding.UTF8, "application/json");
        using HttpResponseMessage resp = await _http.PostAsync("/v2/pdf/sessions/open", content, ct);
        resp.EnsureSuccessStatusCode();
        string body = await resp.Content.ReadAsStringAsync(ct);
        using JsonDocument doc = JsonDocument.Parse(body);
        JsonElement root = doc.RootElement;
        return new PdfSessionOpenResult(
            root.GetProperty("session_id").GetString()!,
            root.GetProperty("page_count").GetInt32(),
            root.GetProperty("file_path").GetString()!);
    }

    public async Task<byte[]> RenderAsync(string sessionId, int page, int size, CancellationToken ct)
    {
        using HttpResponseMessage resp = await _http.GetAsync(
            $"/v2/pdf/sessions/{sessionId}/render?page={page}&size={size}", ct);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadAsByteArrayAsync(ct);
    }

    public async Task<PdfMutateResult> RotateAsync(string sessionId, int[] pages, int angle, CancellationToken ct)
    {
        using StringContent content = new(
            JsonSerializer.Serialize(new { pages, angle }),
            System.Text.Encoding.UTF8, "application/json");
        using HttpResponseMessage resp = await _http.PostAsync(
            $"/v2/pdf/sessions/{sessionId}/rotate", content, ct);
        resp.EnsureSuccessStatusCode();
        string body = await resp.Content.ReadAsStringAsync(ct);
        using JsonDocument doc = JsonDocument.Parse(body);
        return new PdfMutateResult(doc.RootElement.GetProperty("page_count").GetInt32());
    }

    public async Task<PdfMutateResult> DeletePagesAsync(string sessionId, int[] pages, CancellationToken ct)
    {
        using StringContent content = new(
            JsonSerializer.Serialize(new { pages }),
            System.Text.Encoding.UTF8, "application/json");
        using HttpResponseMessage resp = await _http.PostAsync(
            $"/v2/pdf/sessions/{sessionId}/delete_pages", content, ct);
        resp.EnsureSuccessStatusCode();
        string body = await resp.Content.ReadAsStringAsync(ct);
        using JsonDocument doc = JsonDocument.Parse(body);
        return new PdfMutateResult(doc.RootElement.GetProperty("page_count").GetInt32());
    }

    public async Task<string> SaveAsync(string sessionId, string outputPath, CancellationToken ct)
    {
        using StringContent content = new(
            JsonSerializer.Serialize(new { output_path = outputPath }),
            System.Text.Encoding.UTF8, "application/json");
        using HttpResponseMessage resp = await _http.PostAsync(
            $"/v2/pdf/sessions/{sessionId}/save", content, ct);
        resp.EnsureSuccessStatusCode();
        string body = await resp.Content.ReadAsStringAsync(ct);
        using JsonDocument doc = JsonDocument.Parse(body);
        return doc.RootElement.GetProperty("saved_path").GetString()!;
    }

    public async Task CloseAsync(string sessionId, CancellationToken ct)
    {
        using HttpResponseMessage resp = await _http.PostAsync(
            $"/v2/pdf/sessions/{sessionId}/close", content: null, ct);
        resp.EnsureSuccessStatusCode();
    }

    public ValueTask DisposeAsync()
    {
        if (_ownsHttp) _http.Dispose();
        return ValueTask.CompletedTask;
    }
}
