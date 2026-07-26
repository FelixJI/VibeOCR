// Deferred QR client mirroring DeferredInferenceClient. Throws until Attach.
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Inference;

public sealed class DeferredQrCodeClient : IQrCodeClient
{
    private IQrCodeClient? _inner;
    private readonly object _lock = new();

    public bool IsAttached => Volatile.Read(ref _inner) is not null;

    public void Attach(IQrCodeClient client)
    {
        ArgumentNullException.ThrowIfNull(client);
        lock (_lock) _inner = client;
    }

    public void Detach(IQrCodeClient client)
    {
        lock (_lock)
        {
            if (ReferenceEquals(_inner, client)) _inner = null;
        }
    }

    private IQrCodeClient Current =>
        Volatile.Read(ref _inner)
        ?? throw new InvalidOperationException("v2 QR client not attached; atomic switch (Phase 8) pending.");

    public Task<IReadOnlyList<QrCodeDecodedItem>> DecodeAsync(string base64Image, CancellationToken ct)
        => Current.DecodeAsync(base64Image, ct);

    public Task<QrCodeGeneratedImage> GenerateAsync(string data, string format, CancellationToken ct)
        => Current.GenerateAsync(data, format, ct);

    public ValueTask DisposeAsync()
    {
        IQrCodeClient? inner = Interlocked.Exchange(ref _inner, null);
        return inner?.DisposeAsync() ?? ValueTask.CompletedTask;
    }
}
