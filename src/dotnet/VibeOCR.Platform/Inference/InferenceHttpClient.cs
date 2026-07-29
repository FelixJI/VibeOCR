// Phase 7B concrete v2 supervisor client over HttpClient.
//
// Uses the source-generated HttpV2JsonContext for typed (de)serialisation and
// pins the base URL to loopback (defence in depth — the server also enforces
// loopback). All requests carry the Bearer session token.
using System.Text.Json;
using System.Text.Json.Serialization.Metadata;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Runtime.Client;
using VibeOCR.Runtime.Contracts.Generated;

namespace VibeOCR.Platform.Inference;

/// <summary>
/// HttpClient-based v2 supervisor client for WinUI.
/// </summary>
public sealed class InferenceHttpClient : IInferenceClient
{
    private readonly RuntimeHttpClient _runtime;
    private readonly JsonSerializerOptions _options;

    /// <summary>
    /// Create a client. The base URL MUST be loopback; the session token is
    /// sent as a Bearer header on every business request.
    /// </summary>
    public InferenceHttpClient(Uri baseUrl, string sessionToken, HttpMessageHandler? handler = null)
    {
        _options = HttpV2JsonContext.Default.Options;
        _runtime = new RuntimeHttpClient(baseUrl, sessionToken, handler);
    }

    public Uri BaseUrl => _runtime.BaseUrl;

    public async Task<JobRef> SubmitAsync(
        SubmitRequest request,
        IReadOnlyDictionary<string, SubmitUpload> uploads,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        ArgumentNullException.ThrowIfNull(uploads);
        IReadOnlyDictionary<string, SubmitItem> expected = GetExpectedUploads(request);
        ValidateUploads(expected, uploads);

        using MultipartFormDataContent form = _runtime.CreateMultipartContent(
            HttpV2Json.Serialize(request),
            expected.ToDictionary(
                pair => pair.Key,
                pair =>
                {
                    SubmitUpload upload = uploads[pair.Key];
                    return new RuntimeUpload(
                        pair.Value.DisplayName,
                        upload.Content.ToArray(),
                        upload.ContentType ?? "application/octet-stream");
                },
                StringComparer.Ordinal));

        using HttpResponseMessage response = await _runtime.PostAsync(
            RuntimeOperationPaths.SubmitJob, form, cancellationToken)
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

        string path = RuntimeOperationPaths.ObserveJob.Replace(
            "{job_id}", Uri.EscapeDataString(jobId), StringComparison.Ordinal);
        using HttpResponseMessage response = await _runtime.GetAsync(
            $"{path}?after_sequence={afterSequence}",
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
        using StringContent content = _runtime.CreateJsonContent(command, _options);
        using HttpResponseMessage response = await _runtime.PostAsync(
            RuntimeOperationPaths.CommandJob,
            content,
            cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        using JsonDocument document = await _runtime
            .ReadJsonDocumentAsync(response, cancellationToken)
            .ConfigureAwait(false);
        return ParseCommandResult(command, document.RootElement);
    }

    public async Task<ResidencyStatus> GetResidencyAsync(CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await _runtime.GetAsync(
            RuntimeOperationPaths.GetRuntimeResidency, cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        return await ReadAsync<ResidencyStatus>(response, cancellationToken).ConfigureAwait(false);
    }

    public async Task<SettingsSnapshot> GetSettingsAsync(CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await _runtime.GetAsync(
            RuntimeOperationPaths.GetSettings, cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        return await ReadAsync<SettingsSnapshot>(response, cancellationToken).ConfigureAwait(false);
    }

    public async Task<ExportResult> ExportAsync(ExportRequest request, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        using StringContent content = _runtime.CreateJsonContent(
            new
            {
                raw_text = request.RawText,
                markdown_text = request.MarkdownText,
                html_text = request.HtmlText,
                output_path = request.OutputPath,
                format = request.Format,
                overwrite = request.Overwrite,
            },
            _options);
        using HttpResponseMessage response = await _runtime.PostAsync(
            RuntimeOperationPaths.ExportOcr, content, cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        using JsonDocument doc = await _runtime
            .ReadJsonDocumentAsync(response, cancellationToken)
            .ConfigureAwait(false);
        return new ExportResult(
            doc.RootElement.GetProperty("output_path").GetString() ?? string.Empty,
            doc.RootElement.TryGetProperty("bytes_written", out JsonElement bw) ? bw.GetInt64() : 0);
    }

    public async Task<PdfSessionOpenResult> OpenPdfSessionAsync(string path, string? password, CancellationToken ct)
    {
        using StringContent content = _runtime.CreateJsonContent(new { path, password }, _options);
        using HttpResponseMessage resp = await _runtime.PostAsync(
            RuntimeOperationPaths.OpenPdfSession, content, ct);
        await EnsureSuccessAsync(resp, ct);
        using JsonDocument doc = await _runtime.ReadJsonDocumentAsync(resp, ct);
        return new PdfSessionOpenResult(
            doc.RootElement.GetProperty("session_id").GetString()!,
            doc.RootElement.GetProperty("page_count").GetInt32(),
            doc.RootElement.GetProperty("file_path").GetString()!);
    }

    public async Task<byte[]> RenderPdfPageAsync(string sessionId, int page, int size, CancellationToken ct)
    {
        using HttpResponseMessage resp = await _runtime.GetAsync(
            $"{BindSessionPath(RuntimeOperationPaths.RenderPdfPage, sessionId)}?page={page}&size={size}", ct);
        await EnsureSuccessAsync(resp, ct);
        return await _runtime.ReadBinaryAsync(resp, "image/png", ct);
    }

    public async Task<PdfMutateResult> RotatePdfPagesAsync(string sessionId, int[] pages, int angle, CancellationToken ct)
    {
        using StringContent content = _runtime.CreateJsonContent(new { pages, angle }, _options);
        using HttpResponseMessage resp = await _runtime.PostAsync(
            BindSessionPath(RuntimeOperationPaths.RotatePdfPages, sessionId), content, ct);
        await EnsureSuccessAsync(resp, ct);
        using JsonDocument doc = await _runtime.ReadJsonDocumentAsync(resp, ct);
        return new PdfMutateResult(doc.RootElement.GetProperty("page_count").GetInt32());
    }

    public async Task<PdfMutateResult> DeletePdfPagesAsync(string sessionId, int[] pages, CancellationToken ct)
    {
        using StringContent content = _runtime.CreateJsonContent(new { pages }, _options);
        using HttpResponseMessage resp = await _runtime.PostAsync(
            BindSessionPath(RuntimeOperationPaths.DeletePdfPages, sessionId), content, ct);
        await EnsureSuccessAsync(resp, ct);
        using JsonDocument doc = await _runtime.ReadJsonDocumentAsync(resp, ct);
        return new PdfMutateResult(doc.RootElement.GetProperty("page_count").GetInt32());
    }

    public async Task<string> SavePdfAsync(string sessionId, string outputPath, CancellationToken ct)
    {
        using StringContent content = _runtime.CreateJsonContent(
            new { output_path = outputPath },
            _options);
        using HttpResponseMessage resp = await _runtime.PostAsync(
            BindSessionPath(RuntimeOperationPaths.SavePdfSession, sessionId), content, ct);
        await EnsureSuccessAsync(resp, ct);
        using JsonDocument doc = await _runtime.ReadJsonDocumentAsync(resp, ct);
        return doc.RootElement.GetProperty("saved_path").GetString()!;
    }

    public async Task ClosePdfSessionAsync(string sessionId, CancellationToken ct)
    {
        using HttpResponseMessage resp = await _runtime.PostAsync(
            BindSessionPath(RuntimeOperationPaths.ClosePdfSession, sessionId), content: null, ct);
        await EnsureSuccessAsync(resp, ct);
    }

    public async ValueTask DisposeAsync()
    {
        await _runtime.DisposeAsync().ConfigureAwait(false);
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private static string BindSessionPath(string template, string sessionId) =>
        template.Replace(
            "{session_id}", Uri.EscapeDataString(sessionId), StringComparison.Ordinal);

    private async Task EnsureSuccessAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        try
        {
            await _runtime.EnsureSuccessAsync(response, cancellationToken)
                .ConfigureAwait(false);
        }
        catch (RuntimeClientException exc)
        {
            throw new InferenceClientException(
                exc.Code, exc.Message, exc.Retryable, exc.Detail);
        }
    }

    private async Task<T> ReadAsync<T>(HttpResponseMessage response, CancellationToken cancellationToken)
        where T : class
    {
        JsonTypeInfo<T> typeInfo = (JsonTypeInfo<T>)_options.GetTypeInfo(typeof(T));
        try
        {
            return await _runtime
                .ReadJsonAsync(response, typeInfo, cancellationToken)
                .ConfigureAwait(false);
        }
        catch (RuntimeClientException exc)
        {
            throw new InferenceClientException(
                exc.Code, exc.Message, exc.Retryable, exc.Detail);
        }
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

    private static JobCommandResult ParseCommandResult(
        JobCommand command,
        JsonElement root)
    {
        try
        {
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
