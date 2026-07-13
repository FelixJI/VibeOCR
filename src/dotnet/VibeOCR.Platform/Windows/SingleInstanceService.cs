using System.IO.Pipes;
using System.Text;
using System.Text.Json;

namespace VibeOCR.Platform.Windows;

public sealed class SingleInstanceService : IAsyncDisposable
{
    private readonly Func<IReadOnlyList<string>, Task> _argumentHandler;
    private readonly CancellationTokenSource _shutdown = new();
    private readonly Mutex _marker;
    private readonly string _pipeName;
    private readonly Task? _listener;

    public SingleInstanceService(
        string instanceName,
        Func<IReadOnlyList<string>, Task> argumentHandler)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(instanceName);
        _argumentHandler = argumentHandler ?? throw new ArgumentNullException(nameof(argumentHandler));
        string normalized = new(instanceName
            .Where(character => char.IsAsciiLetterOrDigit(character) || character is '-' or '_')
            .ToArray());
        if (normalized.Length is 0 or > 120)
        {
            throw new ArgumentException("Instance name must contain 1–120 safe characters.", nameof(instanceName));
        }

        _pipeName = $"{normalized}-activation";
        _marker = new Mutex(false, $@"Local\{normalized}", out bool createdNew);
        IsPrimary = createdNew;
        if (IsPrimary)
        {
            _listener = Task.Run(ListenAsync);
        }
    }

    public bool IsPrimary { get; }

    public async Task ForwardAsync(
        IReadOnlyList<string> arguments,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(arguments);
        if (IsPrimary)
        {
            throw new InvalidOperationException("The primary instance cannot forward to itself.");
        }

        byte[] payload = JsonSerializer.SerializeToUtf8Bytes(arguments);
        if (payload.Length > 64 * 1024)
        {
            throw new ArgumentException("Forwarded arguments exceed 64 KiB.", nameof(arguments));
        }

        Exception? lastError = null;
        for (int attempt = 0; attempt < 20; attempt++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            await using var client = new NamedPipeClientStream(
                ".",
                _pipeName,
                PipeDirection.Out,
                PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
            try
            {
                await client.ConnectAsync(250, cancellationToken).ConfigureAwait(false);
                await client.WriteAsync(payload, cancellationToken).ConfigureAwait(false);
                await client.FlushAsync(cancellationToken).ConfigureAwait(false);
                return;
            }
            catch (Exception error) when (error is TimeoutException or IOException)
            {
                lastError = error;
            }
        }

        throw new IOException("Primary instance did not accept forwarded arguments.", lastError);
    }

    private async Task ListenAsync()
    {
        while (!_shutdown.IsCancellationRequested)
        {
            try
            {
                await using var server = new NamedPipeServerStream(
                    _pipeName,
                    PipeDirection.In,
                    1,
                    PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly,
                    64 * 1024,
                    64 * 1024);
                await server.WaitForConnectionAsync(_shutdown.Token).ConfigureAwait(false);
                using var buffer = new MemoryStream();
                await server.CopyToAsync(buffer, _shutdown.Token).ConfigureAwait(false);
                if (buffer.Length > 64 * 1024)
                {
                    continue;
                }

                string[] arguments = JsonSerializer.Deserialize<string[]>(buffer.ToArray()) ?? [];
                await _argumentHandler(arguments).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (_shutdown.IsCancellationRequested)
            {
                break;
            }
            catch (Exception) when (!_shutdown.IsCancellationRequested)
            {
                // A malformed activation must not disable future forwarding.
            }
        }
    }

    public async ValueTask DisposeAsync()
    {
        _shutdown.Cancel();
        if (_listener is not null)
        {
            try
            {
                await _listener.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }
        }

        _marker.Dispose();
        _shutdown.Dispose();
    }
}
