// HttpClient-based QR client for the v2 supervisor's /v2/qrcode/* endpoints.
using System.Text.Json;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Runtime.Client;
using VibeOCR.Runtime.Contracts.Generated;

namespace VibeOCR.Platform.Inference;

/// <summary>Concrete IQrCodeClient over HttpClient (loopback, Bearer token).</summary>
public sealed class QrCodeHttpClient : IQrCodeClient
{
    private readonly RuntimeHttpClient _runtime;
    private readonly JsonSerializerOptions _options;

    public QrCodeHttpClient(Uri baseUrl, string sessionToken, HttpMessageHandler? handler = null)
    {
        _options = HttpV2JsonContext.Default.Options;
        _runtime = new RuntimeHttpClient(baseUrl, sessionToken, handler);
    }

    public async Task<IReadOnlyList<QrCodeDecodedItem>> DecodeAsync(
        string base64Image, CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(base64Image);
        using StringContent content = _runtime.CreateJsonContent(
            new { image = base64Image },
            _options);
        using HttpResponseMessage response = await _runtime.PostAsync(
            RuntimeOperationPaths.DecodeQrCode, content, cancellationToken)
            .ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            await ThrowTypedAsync(response, cancellationToken).ConfigureAwait(false);
        }

        using JsonDocument doc = await _runtime
            .ReadJsonDocumentAsync(response, cancellationToken)
            .ConfigureAwait(false);
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
        using StringContent content = _runtime.CreateJsonContent(
            new { data, format },
            _options);
        using HttpResponseMessage response = await _runtime.PostAsync(
            RuntimeOperationPaths.GenerateQrCode, content, cancellationToken)
            .ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            await ThrowTypedAsync(response, cancellationToken).ConfigureAwait(false);
        }

        using JsonDocument doc = await _runtime
            .ReadJsonDocumentAsync(response, cancellationToken)
            .ConfigureAwait(false);
        return new QrCodeGeneratedImage(
            doc.RootElement.GetProperty("image").GetString() ?? string.Empty,
            doc.RootElement.TryGetProperty("media_type", out JsonElement mt) ? (mt.GetString() ?? "image/png") : "image/png");
    }

    public ValueTask DisposeAsync()
    {
        return _runtime.DisposeAsync();
    }

    private async Task ThrowTypedAsync(HttpResponseMessage response, CancellationToken cancellationToken)
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
}
