// HttpClient-based QR client for the v2 supervisor's /v2/qrcode/* endpoints.
using System.Net.Http.Headers;
using System.Text.Json;
using VibeOCR.Contracts.HttpV2;

namespace VibeOCR.Platform.Inference;

/// <summary>Concrete IQrCodeClient over HttpClient (loopback, Bearer token).</summary>
public sealed class QrCodeHttpClient : IQrCodeClient
{
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _options;

    public QrCodeHttpClient(Uri baseUrl, string sessionToken, HttpMessageHandler? handler = null)
    {
        if (!IsLoopback(baseUrl))
        {
            throw new ArgumentException("QrCodeHttpClient refuses non-loopback base URL.", nameof(baseUrl));
        }

        _options = HttpV2JsonContext.Default.Options;
        _http = handler is null ? new HttpClient() : new HttpClient(handler);
        _http.BaseAddress = baseUrl;
        _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", sessionToken);
    }

    public async Task<IReadOnlyList<QrCodeDecodedItem>> DecodeAsync(
        string base64Image, CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(base64Image);
        using StringContent content = new(
            JsonSerializer.Serialize(new { image = base64Image }, _options),
            System.Text.Encoding.UTF8,
            "application/json");
        using HttpResponseMessage response = await _http.PostAsync("/v2/qrcode/decode", content, cancellationToken)
            .ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            await ThrowTypedAsync(response, cancellationToken).ConfigureAwait(false);
        }

        string body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        using JsonDocument doc = JsonDocument.Parse(body);
        var items = new List<QrCodeDecodedItem>();
        foreach (JsonElement code in doc.RootElement.GetProperty("codes").EnumerateArray())
        {
            items.Add(new QrCodeDecodedItem(
                code.GetProperty("data").GetString() ?? string.Empty,
                code.TryGetProperty("format", out JsonElement fmt) ? (fmt.GetString() ?? "QR") : "QR",
                code.TryGetProperty("is_url", out JsonElement isUrl) && isUrl.GetBoolean()));
        }

        return items;
    }

    public async Task<QrCodeGeneratedImage> GenerateAsync(
        string data, string format, CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(data);
        using StringContent content = new(
            JsonSerializer.Serialize(new { data, format }, _options),
            System.Text.Encoding.UTF8,
            "application/json");
        using HttpResponseMessage response = await _http.PostAsync("/v2/qrcode/generate", content, cancellationToken)
            .ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            await ThrowTypedAsync(response, cancellationToken).ConfigureAwait(false);
        }

        string body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        using JsonDocument doc = JsonDocument.Parse(body);
        return new QrCodeGeneratedImage(
            doc.RootElement.GetProperty("image").GetString() ?? string.Empty,
            doc.RootElement.TryGetProperty("media_type", out JsonElement mt) ? (mt.GetString() ?? "image/png") : "image/png");
    }

    public ValueTask DisposeAsync()
    {
        _http.Dispose();

        return ValueTask.CompletedTask;
    }

    private static async Task ThrowTypedAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        try
        {
            string body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
            HttpV2ErrorPayload? payload = JsonSerializer.Deserialize<HttpV2ErrorPayload>(body, HttpV2JsonContext.Default.Options);
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
            // Fall through.
        }

        throw new InferenceClientException(
            HttpV2ErrorCode.InternalError,
            $"Unexpected HTTP {(int)response.StatusCode} from supervisor QR endpoint.",
            retryable: true);
    }

    private static bool IsLoopback(Uri uri) => uri.Host is "127.0.0.1" or "localhost" or "::1";
}
