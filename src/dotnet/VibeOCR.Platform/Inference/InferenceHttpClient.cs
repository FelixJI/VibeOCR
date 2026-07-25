// Phase 7B concrete v2 supervisor client over HttpClient.
//
// Uses the source-generated HttpV2JsonContext for typed (de)serialisation and
// pins the base URL to loopback (defence in depth — the server also enforces
// loopback). All requests carry the Bearer session token.
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization.Metadata;
using VibeOCR.Contracts.HttpV2;

namespace VibeOCR.Platform.Inference;

/// <summary>
/// HttpClient-based v2 supervisor client for WinUI.
/// </summary>
public sealed class InferenceHttpClient : IInferenceClient
{
    private readonly HttpClient _http;
    private readonly bool _ownsHttp;
    private readonly JsonSerializerOptions _options;

    /// <summary>
    /// Create a client. The base URL MUST be loopback; the session token is
    /// sent as a Bearer header on every business request.
    /// </summary>
    public InferenceHttpClient(Uri baseUrl, string sessionToken, HttpMessageHandler? handler = null)
    {
        ArgumentNullException.ThrowIfNull(baseUrl);
        ArgumentException.ThrowIfNullOrWhiteSpace(sessionToken);
        if (!IsLoopback(baseUrl))
        {
            throw new ArgumentException(
                "InferenceHttpClient refuses non-loopback base URL.", nameof(baseUrl));
        }

        _options = HttpV2JsonContext.Default.Options;
        _ownsHttp = handler is not null;
        _http = handler is null ? new HttpClient() : new HttpClient(handler);
        _http.BaseAddress = baseUrl;
        _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", sessionToken);
        _http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
    }

    public Uri BaseUrl => _http.BaseAddress!;

    public async Task<JobRef> SubmitRecognitionAsync(
        IReadOnlyList<RecognitionUpload> uploads,
        JobPriority priority,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(uploads);
        if (uploads.Count == 0)
        {
            throw new ArgumentException("At least one upload is required.", nameof(uploads));
        }

        using var form = new MultipartFormDataContent();
        foreach (RecognitionUpload upload in uploads)
        {
            var bytes = new ByteArrayContent(upload.Content.ToArray());
            bytes.Headers.ContentType = new MediaTypeHeaderValue(
                string.IsNullOrWhiteSpace(upload.ContentType) ? "application/octet-stream" : upload.ContentType);
            form.Add(bytes, "files", upload.FileName);
        }

        using HttpResponseMessage response = await _http.PostAsync("/v2/jobs/recognition", form, cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        return await ReadAsync<JobRef>(response, cancellationToken).ConfigureAwait(false);
    }

    public async Task<JobSnapshot> GetJobAsync(string jobId, CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(jobId);
        using HttpResponseMessage response = await _http.GetAsync($"/v2/jobs/{jobId}", cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        return await ReadAsync<JobSnapshot>(response, cancellationToken).ConfigureAwait(false);
    }

    public async Task<IReadOnlyList<StageEvent>> GetEventsAsync(
        string jobId, int afterSequence, CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(jobId);
        using HttpResponseMessage response = await _http.GetAsync(
            $"/v2/jobs/{jobId}/events?after_sequence={afterSequence}", cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        EventsEnvelope? envelope = await ReadAsync<EventsEnvelope>(response, cancellationToken).ConfigureAwait(false);
        return envelope?.Events ?? Array.Empty<StageEvent>();
    }

    public async Task<IReadOnlyList<ResultEntry>> GetResultAsync(string jobId, CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(jobId);
        using HttpResponseMessage response = await _http.GetAsync($"/v2/jobs/{jobId}/result", cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        ResultsEnvelope? envelope = await ReadAsync<ResultsEnvelope>(response, cancellationToken).ConfigureAwait(false);
        return envelope?.Results ?? Array.Empty<ResultEntry>();
    }

    public async Task<CancelMode> CancelAsync(string jobId, CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(jobId);
        using HttpResponseMessage response = await _http.PostAsync($"/v2/jobs/{jobId}/cancel", content: null, cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        CancelAck? ack = await ReadAsync<CancelAck>(response, cancellationToken).ConfigureAwait(false);
        return ack?.CancelMode ?? CancelMode.Cooperative;
    }

    public async Task DeleteJobAsync(string jobId, CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(jobId);
        using HttpResponseMessage response = await _http.DeleteAsync($"/v2/jobs/{jobId}", cancellationToken)
            .ConfigureAwait(false);
        // 204 is the success code for delete.
        if (!response.IsSuccessStatusCode && (int)response.StatusCode != 204)
        {
            await ThrowTypedAsync(response, cancellationToken).ConfigureAwait(false);
        }
    }

    public async Task<ResidencyStatus> GetResidencyAsync(CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await _http.GetAsync("/v2/runtime/residency", cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        return await ReadAsync<ResidencyStatus>(response, cancellationToken).ConfigureAwait(false);
    }

    public async Task<SettingsSnapshot> GetSettingsAsync(CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await _http.GetAsync("/v2/settings", cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        return await ReadAsync<SettingsSnapshot>(response, cancellationToken).ConfigureAwait(false);
    }

    public async Task<ExportResult> ExportAsync(ExportRequest request, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        using StringContent content = new(
            JsonSerializer.Serialize(new
            {
                raw_text = request.RawText,
                markdown_text = request.MarkdownText,
                html_text = request.HtmlText,
                output_path = request.OutputPath,
                format = request.Format,
                overwrite = request.Overwrite,
            }, _options),
            System.Text.Encoding.UTF8,
            "application/json");
        using HttpResponseMessage response = await _http.PostAsync("/v2/export", content, cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        string body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        using JsonDocument doc = JsonDocument.Parse(body);
        return new ExportResult(
            doc.RootElement.GetProperty("output_path").GetString() ?? string.Empty,
            doc.RootElement.TryGetProperty("bytes_written", out JsonElement bw) ? bw.GetInt64() : 0);
    }

    public async Task<PdfSessionOpenResult> OpenPdfSessionAsync(string path, string? password, CancellationToken ct)
    {
        using StringContent content = new(JsonSerializer.Serialize(new { path, password }), System.Text.Encoding.UTF8, "application/json");
        using HttpResponseMessage resp = await _http.PostAsync("/v2/pdf/sessions/open", content, ct);
        await EnsureSuccessAsync(resp, ct);
        string body = await resp.Content.ReadAsStringAsync(ct);
        using JsonDocument doc = JsonDocument.Parse(body);
        return new PdfSessionOpenResult(
            doc.RootElement.GetProperty("session_id").GetString()!,
            doc.RootElement.GetProperty("page_count").GetInt32(),
            doc.RootElement.GetProperty("file_path").GetString()!);
    }

    public async Task<byte[]> RenderPdfPageAsync(string sessionId, int page, int size, CancellationToken ct)
    {
        using HttpResponseMessage resp = await _http.GetAsync($"/v2/pdf/sessions/{sessionId}/render?page={page}&size={size}", ct);
        await EnsureSuccessAsync(resp, ct);
        return await resp.Content.ReadAsByteArrayAsync(ct);
    }

    public async Task<PdfMutateResult> RotatePdfPagesAsync(string sessionId, int[] pages, int angle, CancellationToken ct)
    {
        using StringContent content = new(JsonSerializer.Serialize(new { pages, angle }), System.Text.Encoding.UTF8, "application/json");
        using HttpResponseMessage resp = await _http.PostAsync($"/v2/pdf/sessions/{sessionId}/rotate", content, ct);
        await EnsureSuccessAsync(resp, ct);
        string body = await resp.Content.ReadAsStringAsync(ct);
        using JsonDocument doc = JsonDocument.Parse(body);
        return new PdfMutateResult(doc.RootElement.GetProperty("page_count").GetInt32());
    }

    public async Task<PdfMutateResult> DeletePdfPagesAsync(string sessionId, int[] pages, CancellationToken ct)
    {
        using StringContent content = new(JsonSerializer.Serialize(new { pages }), System.Text.Encoding.UTF8, "application/json");
        using HttpResponseMessage resp = await _http.PostAsync($"/v2/pdf/sessions/{sessionId}/delete_pages", content, ct);
        await EnsureSuccessAsync(resp, ct);
        string body = await resp.Content.ReadAsStringAsync(ct);
        using JsonDocument doc = JsonDocument.Parse(body);
        return new PdfMutateResult(doc.RootElement.GetProperty("page_count").GetInt32());
    }

    public async Task<string> SavePdfAsync(string sessionId, string outputPath, CancellationToken ct)
    {
        using StringContent content = new(JsonSerializer.Serialize(new { output_path = outputPath }), System.Text.Encoding.UTF8, "application/json");
        using HttpResponseMessage resp = await _http.PostAsync($"/v2/pdf/sessions/{sessionId}/save", content, ct);
        await EnsureSuccessAsync(resp, ct);
        string body = await resp.Content.ReadAsStringAsync(ct);
        using JsonDocument doc = JsonDocument.Parse(body);
        return doc.RootElement.GetProperty("saved_path").GetString()!;
    }

    public async Task ClosePdfSessionAsync(string sessionId, CancellationToken ct)
    {
        using HttpResponseMessage resp = await _http.PostAsync($"/v2/pdf/sessions/{sessionId}/close", content: null, ct);
        await EnsureSuccessAsync(resp, ct);
    }

    public async ValueTask DisposeAsync()
    {
        if (_ownsHttp)
        {
            _http.Dispose();
        }
        await ValueTask.CompletedTask.ConfigureAwait(false);
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private async Task EnsureSuccessAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }

        await ThrowTypedAsync(response, cancellationToken).ConfigureAwait(false);
    }

    private async Task ThrowTypedAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        try
        {
            string body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
            HttpV2ErrorPayload? payload = JsonSerializer.Deserialize<HttpV2ErrorPayload>(body, _options);
            if (payload is not null)
            {
                throw new InferenceClientException(payload.Code, payload.Message, payload.Retryable, payload.Detail);
            }
        }
        catch (InferenceClientException)
        {
            throw;
        }
        catch
        {
            // Fall through to the generic error below.
        }

        throw new InferenceClientException(
            HttpV2ErrorCode.InternalError,
            $"Unexpected HTTP {(int)response.StatusCode} from supervisor.",
            retryable: true);
    }

    private async Task<T> ReadAsync<T>(HttpResponseMessage response, CancellationToken cancellationToken)
        where T : class
    {
        JsonTypeInfo<T> typeInfo = (JsonTypeInfo<T>)_options.GetTypeInfo(typeof(T));
        string body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        T? value = JsonSerializer.Deserialize(body, typeInfo);
        return value
            ?? throw new InferenceClientException(
                HttpV2ErrorCode.InternalError, "Empty supervisor response.", retryable: false);
    }

    private static bool IsLoopback(Uri uri)
    {
        string host = uri.Host;
        return host is "127.0.0.1" or "localhost" or "::1";
    }

    // Envelope shapes returned by the events/result endpoints live in
    // VibeOCR.Contracts.HttpV2 so the source-generated context can register them.
}
