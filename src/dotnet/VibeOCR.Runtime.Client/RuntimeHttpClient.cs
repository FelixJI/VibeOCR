using System.Net.Http.Headers;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization.Metadata;
using VibeOCR.Contracts.HttpV2;

namespace VibeOCR.Runtime.Client;

/// <summary>
/// Protocol-owned HTTP transport. Product adapters supply generated operation
/// paths and remain responsible only for product-specific DTO projection.
/// </summary>
public sealed class RuntimeHttpClient : IAsyncDisposable
{
    private readonly HttpClient _http;
    private readonly Uri _baseUrl;

    public RuntimeHttpClient(
        Uri baseUrl,
        string sessionToken,
        HttpMessageHandler? handler = null)
    {
        ArgumentNullException.ThrowIfNull(baseUrl);
        ArgumentException.ThrowIfNullOrWhiteSpace(sessionToken);
        if (!IsSafeBaseUrl(baseUrl))
        {
            throw new ArgumentException(
                "Runtime client refuses non-loopback base URL.",
                nameof(baseUrl));
        }

        _baseUrl = baseUrl;
        _http = handler is null ? new HttpClient() : new HttpClient(handler);
        _http.BaseAddress = baseUrl;
        _http.DefaultRequestHeaders.Authorization =
            new AuthenticationHeaderValue("Bearer", sessionToken);
        _http.DefaultRequestHeaders.Accept.Add(
            new MediaTypeWithQualityHeaderValue("application/json"));
    }

    public Uri BaseUrl => _http.BaseAddress!;

    public Task<HttpResponseMessage> GetAsync(
        string path,
        CancellationToken cancellationToken) =>
        _http.GetAsync(RequireRelativePath(path), cancellationToken);

    public Task<HttpResponseMessage> PostAsync(
        string path,
        HttpContent? content,
        CancellationToken cancellationToken) =>
        _http.PostAsync(RequireRelativePath(path), content, cancellationToken);

    public Task<HttpResponseMessage> PutAsync(
        string path,
        HttpContent? content,
        CancellationToken cancellationToken) =>
        _http.PutAsync(RequireRelativePath(path), content, cancellationToken);

    public StringContent CreateJsonContent<T>(
        T value,
        JsonSerializerOptions? options = null) =>
        new(
            JsonSerializer.Serialize(value, options),
            Encoding.UTF8,
            "application/json");

    public MultipartFormDataContent CreateMultipartContent(
        string manifestJson,
        IReadOnlyDictionary<string, RuntimeUpload> uploads)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(manifestJson);
        ArgumentNullException.ThrowIfNull(uploads);

        var form = new MultipartFormDataContent();
        form.Add(
            new StringContent(manifestJson, Encoding.UTF8, "application/json"),
            "manifest");
        foreach ((string name, RuntimeUpload upload) in uploads)
        {
            if (string.IsNullOrWhiteSpace(name))
            {
                form.Dispose();
                throw new ArgumentException(
                    "Multipart field names must be non-empty.",
                    nameof(uploads));
            }

            var bytes = new ByteArrayContent(upload.Content.ToArray());
            bytes.Headers.ContentType = new MediaTypeHeaderValue(
                string.IsNullOrWhiteSpace(upload.MediaType)
                    ? "application/octet-stream"
                    : upload.MediaType);
            form.Add(bytes, name, upload.FileName);
        }

        return form;
    }

    public async Task<T> ReadJsonAsync<T>(
        HttpResponseMessage response,
        JsonTypeInfo<T> typeInfo,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(response);
        ArgumentNullException.ThrowIfNull(typeInfo);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        await using Stream stream = await response.Content
            .ReadAsStreamAsync(cancellationToken)
            .ConfigureAwait(false);
        T? value = await JsonSerializer
            .DeserializeAsync(stream, typeInfo, cancellationToken)
            .ConfigureAwait(false);
        return value ?? throw new RuntimeClientException(
            HttpV2ErrorCode.AdapterProtocolViolation,
            "Runtime API returned an empty JSON response.",
            retryable: false);
    }

    public async Task<JsonDocument> ReadJsonDocumentAsync(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(response);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        await using Stream stream = await response.Content
            .ReadAsStreamAsync(cancellationToken)
            .ConfigureAwait(false);
        return await JsonDocument.ParseAsync(
                stream,
                cancellationToken: cancellationToken)
            .ConfigureAwait(false);
    }

    public async Task<byte[]> ReadBinaryAsync(
        HttpResponseMessage response,
        string expectedMediaType,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(response);
        ArgumentException.ThrowIfNullOrWhiteSpace(expectedMediaType);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        string? actual = response.Content.Headers.ContentType?.MediaType;
        if (!string.Equals(actual, expectedMediaType, StringComparison.OrdinalIgnoreCase))
        {
            throw new RuntimeClientException(
                HttpV2ErrorCode.AdapterProtocolViolation,
                $"Runtime API returned media type '{actual ?? "<missing>"}'; "
                    + $"expected '{expectedMediaType}'.",
                retryable: false);
        }

        return await response.Content
            .ReadAsByteArrayAsync(cancellationToken)
            .ConfigureAwait(false);
    }

    public async IAsyncEnumerable<T> ReadNdjsonAsync<T>(
        HttpResponseMessage response,
        JsonTypeInfo<T> typeInfo,
        [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(response);
        ArgumentNullException.ThrowIfNull(typeInfo);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        string? mediaType = response.Content.Headers.ContentType?.MediaType;
        if (!string.Equals(
                mediaType,
                "application/x-ndjson",
                StringComparison.OrdinalIgnoreCase))
        {
            throw new RuntimeClientException(
                HttpV2ErrorCode.AdapterProtocolViolation,
                $"Runtime API returned media type '{mediaType ?? "<missing>"}'; "
                    + "expected 'application/x-ndjson'.",
                retryable: false);
        }

        await using Stream stream = await response.Content
            .ReadAsStreamAsync(cancellationToken)
            .ConfigureAwait(false);
        using var reader = new StreamReader(stream, Encoding.UTF8);
        while (await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false)
            is { } line)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }

            T? value = JsonSerializer.Deserialize(line, typeInfo);
            yield return value ?? throw new RuntimeClientException(
                HttpV2ErrorCode.AdapterProtocolViolation,
                "Runtime API returned an empty NDJSON item.",
                retryable: false);
        }
    }

    public async Task EnsureSuccessAsync(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }

        try
        {
            string body = await response.Content
                .ReadAsStringAsync(cancellationToken)
                .ConfigureAwait(false);
            HttpV2ErrorPayload? payload = HttpV2Json.Deserialize<HttpV2ErrorPayload>(body);
            if (payload is not null)
            {
                throw new RuntimeClientException(
                    payload.Code,
                    payload.Message,
                    payload.Retryable,
                    payload.Detail);
            }
        }
        catch (RuntimeClientException)
        {
            throw;
        }
        catch
        {
            // Preserve a stable typed boundary for malformed server responses.
        }

        throw new RuntimeClientException(
            HttpV2ErrorCode.InternalError,
            $"Unexpected HTTP {(int)response.StatusCode} from Runtime API.",
            retryable: true);
    }

    public ValueTask DisposeAsync()
    {
        _http.Dispose();
        return ValueTask.CompletedTask;
    }

    private Uri RequireRelativePath(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        if (!path.StartsWith("/", StringComparison.Ordinal)
            || path.StartsWith("//", StringComparison.Ordinal)
            || path.Contains('\\')
            || path.Any(char.IsControl)
            || path.Contains('{')
            || path.Contains('}'))
        {
            throw new ArgumentException(
                "Runtime path must be a bound root-relative operation path.",
                nameof(path));
        }

        if (!Uri.TryCreate(_baseUrl, path, out Uri? resolved)
            || !IsLoopback(resolved)
            || !string.Equals(
                resolved.Scheme,
                _baseUrl.Scheme,
                StringComparison.OrdinalIgnoreCase)
            || !string.Equals(
                resolved.Host,
                _baseUrl.Host,
                StringComparison.OrdinalIgnoreCase)
            || resolved.Port != _baseUrl.Port)
        {
            throw new ArgumentException(
                "Runtime path escaped the configured loopback authority.",
                nameof(path));
        }

        return resolved;
    }

    private static bool IsSafeBaseUrl(Uri uri) =>
        uri.IsAbsoluteUri
        && string.IsNullOrEmpty(uri.UserInfo)
        && string.IsNullOrEmpty(uri.Query)
        && string.IsNullOrEmpty(uri.Fragment)
        && uri.AbsolutePath == "/"
        && IsLoopback(uri);

    private static bool IsLoopback(Uri uri) =>
        uri.Scheme is "http" or "https"
        && uri.IsLoopback;
}

public sealed record RuntimeUpload(
    string FileName,
    ReadOnlyMemory<byte> Content,
    string MediaType = "application/octet-stream");
