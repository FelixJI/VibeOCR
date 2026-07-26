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
        _http = handler is null ? new HttpClient() : new HttpClient(handler);
        _http.BaseAddress = baseUrl;
        _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", sessionToken);
        _http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
    }

    public Uri BaseUrl => _http.BaseAddress!;

    public async Task<JobRef> SubmitAsync(
        SubmitRequest request,
        IReadOnlyDictionary<string, SubmitUpload> uploads,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        ArgumentNullException.ThrowIfNull(uploads);
        IReadOnlyDictionary<string, SubmitItem> expected = GetExpectedUploads(request);
        ValidateUploads(expected, uploads);

        using var form = new MultipartFormDataContent();
        form.Add(new StringContent(
            HttpV2Json.Serialize(request),
            System.Text.Encoding.UTF8,
            "application/json"), "manifest");
        foreach ((string attachment, SubmitItem item) in expected)
        {
            SubmitUpload upload = uploads[attachment];
            var bytes = new ByteArrayContent(upload.Content.ToArray());
            bytes.Headers.ContentType = new MediaTypeHeaderValue(
                string.IsNullOrWhiteSpace(upload.ContentType) ? "application/octet-stream" : upload.ContentType);
            form.Add(bytes, attachment, item.DisplayName);
        }

        using HttpResponseMessage response = await _http.PostAsync("/v2/jobs", form, cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        return await ReadAsync<JobRef>(response, cancellationToken).ConfigureAwait(false);
    }

    public async Task<JobUpdate> ObserveAsync(
        string jobId,
        int afterSequence,
        CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(jobId);
        if (afterSequence < 0)
        {
            throw new ArgumentOutOfRangeException(
                nameof(afterSequence), afterSequence, "Sequence must be non-negative.");
        }

        using HttpResponseMessage response = await _http.GetAsync(
            $"/v2/jobs/{Uri.EscapeDataString(jobId)}/observe?after_sequence={afterSequence}",
            cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        return await ReadAsync<JobUpdate>(response, cancellationToken).ConfigureAwait(false);
    }

    public async Task<JobCommandResult> CommandAsync(
        JobCommand command,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(command);
        using StringContent content = new(
            HttpV2Json.Serialize(command),
            System.Text.Encoding.UTF8,
            "application/json");
        using HttpResponseMessage response = await _http.PostAsync(
            "/v2/jobs/command",
            content,
            cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        string body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        return ParseCommandResult(command, body);
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
        _http.Dispose();
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

    private static IReadOnlyDictionary<string, SubmitItem> GetExpectedUploads(SubmitRequest request)
    {
        var expected = new Dictionary<string, SubmitItem>(StringComparer.Ordinal);
        foreach (SubmitItem item in request.Items)
        {
            if (!item.Source.TryGetValue("type", out JsonElement sourceType)
                || sourceType.ValueKind != JsonValueKind.String
                || sourceType.GetString() != "upload.v1")
            {
                continue;
            }

            if (!item.Source.TryGetValue("attachment", out JsonElement attachmentElement)
                || attachmentElement.ValueKind != JsonValueKind.String
                || string.IsNullOrWhiteSpace(attachmentElement.GetString()))
            {
                throw new ArgumentException(
                    $"Upload item '{item.ClientItemKey}' must name a non-empty attachment.",
                    nameof(request));
            }

            string attachment = attachmentElement.GetString()!;
            if (!expected.TryAdd(attachment, item))
            {
                throw new ArgumentException(
                    $"Attachment '{attachment}' is referenced more than once.",
                    nameof(request));
            }
        }

        return expected;
    }

    private static void ValidateUploads(
        IReadOnlyDictionary<string, SubmitItem> expected,
        IReadOnlyDictionary<string, SubmitUpload> uploads)
    {
        if (expected.Count != uploads.Count
            || expected.Keys.Any(key => !uploads.ContainsKey(key)))
        {
            throw new ArgumentException(
                "Uploads must exactly match the manifest's upload attachments.",
                nameof(uploads));
        }

        foreach ((string attachment, SubmitUpload? upload) in uploads)
        {
            if (string.IsNullOrWhiteSpace(attachment) || upload is null)
            {
                throw new ArgumentException(
                    "Upload attachment names and values must be non-empty.",
                    nameof(uploads));
            }
        }
    }

    private static JobCommandResult ParseCommandResult(JobCommand command, string body)
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(body);
            JsonElement root = document.RootElement;
            string commandId = root.GetProperty("command_id").GetString()
                ?? throw new JsonException("command_id must be a string.");
            string kind = root.GetProperty("kind").GetString()
                ?? throw new JsonException("kind must be a string.");
            if (!string.Equals(commandId, command.CommandId, StringComparison.Ordinal)
                || !string.Equals(kind, CommandKindWireName(command.Kind), StringComparison.Ordinal))
            {
                throw new JsonException("Command response does not match its request.");
            }

            CancelMode? cancelMode = null;
            if (root.TryGetProperty("cancel_mode", out JsonElement cancelElement)
                && cancelElement.ValueKind != JsonValueKind.Null)
            {
                cancelMode = cancelElement.GetString() switch
                {
                    "queued_only" => Contracts.HttpV2.CancelMode.QueuedOnly,
                    "cooperative" => Contracts.HttpV2.CancelMode.Cooperative,
                    "forced" => Contracts.HttpV2.CancelMode.Forced,
                    _ => throw new JsonException("Unknown cancel_mode."),
                };
            }

            JobRef? jobRef = null;
            if (root.TryGetProperty("job_ref", out JsonElement jobRefElement)
                && jobRefElement.ValueKind != JsonValueKind.Null)
            {
                jobRef = HttpV2Json.Deserialize<JobRef>(jobRefElement.GetRawText())
                    ?? throw new JsonException("job_ref must be an object.");
            }

            bool shapeIsValid = command.Kind switch
            {
                JobCommandKind.Cancel => cancelMode is not null && jobRef is null,
                JobCommandKind.Retry => cancelMode is null && jobRef is not null,
                JobCommandKind.Forget => cancelMode is null && jobRef is null,
                _ => false,
            };
            if (!shapeIsValid)
            {
                throw new JsonException(
                    $"Command result payload does not match '{CommandKindWireName(command.Kind)}'.");
            }

            return new JobCommandResult(commandId, command.Kind, cancelMode, jobRef);
        }
        catch (JsonException exception)
        {
            throw new InferenceClientException(
                HttpV2ErrorCode.InternalError,
                $"Invalid command response: {exception.Message}",
                retryable: false);
        }
    }

    private static string CommandKindWireName(JobCommandKind kind) => kind switch
    {
        JobCommandKind.Cancel => "cancel",
        JobCommandKind.Retry => "retry",
        JobCommandKind.Forget => "forget",
        _ => throw new ArgumentOutOfRangeException(nameof(kind), kind, "Unknown command kind."),
    };

}
