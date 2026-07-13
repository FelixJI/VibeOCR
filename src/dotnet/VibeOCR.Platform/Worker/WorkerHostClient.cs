using System.Collections.Concurrent;
using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using VibeOCR.Contracts;

namespace VibeOCR.Platform.Worker;

public sealed class WorkerHostClient : IAsyncDisposable
{
    private readonly Stream _stream;
    private readonly TimeSpan _defaultTimeout;
    private readonly ConcurrentDictionary<Guid, IPendingCall> _pending = new();
    private readonly ConcurrentDictionary<Guid, long> _eventSequences = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly CancellationTokenSource _shutdown = new();
    private readonly Task _readerTask;

    public WorkerHostClient(Stream stream, TimeSpan defaultTimeout)
    {
        _stream = stream ?? throw new ArgumentNullException(nameof(stream));
        if (defaultTimeout <= TimeSpan.Zero)
        {
            throw new ArgumentOutOfRangeException(nameof(defaultTimeout));
        }

        _defaultTimeout = defaultTimeout;
        _readerTask = Task.Run(ReadLoopAsync);
    }

    public event Func<RpcEventEnvelope, ValueTask>? EventReceived;

    public static async Task<WorkerHostClient> ConnectAsync(
        string pipeName,
        string sessionToken,
        TimeSpan connectTimeout,
        TimeSpan callTimeout,
        CancellationToken cancellationToken)
    {
        string normalized = pipeName.StartsWith(@"\\.\pipe\", StringComparison.OrdinalIgnoreCase)
            ? pipeName[9..]
            : pipeName;
        var pipe = new NamedPipeClientStream(
            ".",
            normalized,
            PipeDirection.InOut,
            PipeOptions.Asynchronous | PipeOptions.WriteThrough);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(connectTimeout);
        try
        {
            await pipe.ConnectAsync(timeout.Token).ConfigureAwait(false);
            await FrameCodec.WriteAsync(
                pipe,
                Encoding.UTF8.GetBytes(sessionToken),
                timeout.Token,
                maxFrameBytes: 256).ConfigureAwait(false);
            return new WorkerHostClient(pipe, callTimeout);
        }
        catch
        {
            await pipe.DisposeAsync().ConfigureAwait(false);
            throw;
        }
    }

    public async Task<TResponse> CallAsync<TRequest, TResponse>(
        string method,
        TRequest request,
        CancellationToken cancellationToken)
        where TRequest : IProtocolValidatable
        where TResponse : IProtocolValidatable
    {
        Guid requestId = Guid.NewGuid();
        Guid taskId = Guid.NewGuid();
        long deadline = DateTimeOffset.UtcNow.Add(_defaultTimeout).ToUnixTimeMilliseconds();
        var envelope = new RpcRequestEnvelope<TRequest>
        {
            ProtocolVersion = ProtocolConstants.Version,
            RequestId = requestId,
            TaskId = taskId,
            Method = method,
            Payload = request,
            DeadlineUnixMs = deadline,
        };
        envelope.Validate();
        var pending = new PendingCall<TResponse>(method);
        if (!_pending.TryAdd(requestId, pending))
        {
            throw new InvalidOperationException("Duplicate WorkerHost request id.");
        }

        try
        {
            await WriteEnvelopeAsync(envelope, _shutdown.Token).ConfigureAwait(false);
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(
                cancellationToken,
                _shutdown.Token);
            timeout.CancelAfter(_defaultTimeout);
            try
            {
                return await pending.Task.WaitAsync(timeout.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (!_shutdown.IsCancellationRequested)
            {
                await SendCancelAsync(taskId).ConfigureAwait(false);
                cancellationToken.ThrowIfCancellationRequested();
                throw new TimeoutException($"WorkerHost call {method} exceeded {_defaultTimeout}.");
            }
        }
        finally
        {
            _pending.TryRemove(requestId, out _);
        }
    }

    private async Task SendCancelAsync(Guid originalTaskId)
    {
        Guid requestId = Guid.NewGuid();
        var envelope = new RpcRequestEnvelope<CancelRequest>
        {
            ProtocolVersion = ProtocolConstants.Version,
            RequestId = requestId,
            TaskId = Guid.NewGuid(),
            Method = RpcMethods.Cancel,
            Payload = new CancelRequest { TaskId = originalTaskId },
            DeadlineUnixMs = DateTimeOffset.UtcNow.AddSeconds(5).ToUnixTimeMilliseconds(),
        };
        await WriteEnvelopeAsync(envelope, CancellationToken.None).ConfigureAwait(false);
    }

    private async Task WriteEnvelopeAsync(object envelope, CancellationToken cancellationToken)
    {
        byte[] payload = Encoding.UTF8.GetBytes(ProtocolJson.Serialize(envelope));
        await _writeLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            await FrameCodec.WriteAsync(_stream, payload, cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            _writeLock.Release();
        }
    }

    private async Task ReadLoopAsync()
    {
        Exception? terminalError = null;
        try
        {
            while (!_shutdown.IsCancellationRequested)
            {
                byte[] frame = await FrameCodec.ReadAsync(_stream, _shutdown.Token)
                    .ConfigureAwait(false);
                using JsonDocument document = JsonDocument.Parse(frame);
                JsonElement root = document.RootElement;
                if (root.TryGetProperty("event", out _))
                {
                    await DispatchEventAsync(root).ConfigureAwait(false);
                    continue;
                }

                if (!root.TryGetProperty("request_id", out JsonElement requestIdElement) ||
                    !Guid.TryParse(requestIdElement.GetString(), out Guid requestId))
                {
                    throw new ProtocolContractException("Response is missing request_id.");
                }

                if (_pending.TryRemove(requestId, out IPendingCall? pending))
                {
                    pending.Complete(root);
                }
            }
        }
        catch (OperationCanceledException) when (_shutdown.IsCancellationRequested)
        {
        }
        catch (Exception error)
        {
            terminalError = error;
        }
        finally
        {
            Exception failure = terminalError ?? new EndOfStreamException("WorkerHost connection closed.");
            foreach ((Guid id, IPendingCall pending) in _pending)
            {
                if (_pending.TryRemove(id, out _))
                {
                    pending.Fail(failure);
                }
            }
        }
    }

    private async ValueTask DispatchEventAsync(JsonElement root)
    {
        var envelope = (RpcEventEnvelope)ProtocolJson.DeserializeEvent(root);
        long previous = _eventSequences.GetOrAdd(envelope.TaskId, -1);
        if (envelope.Sequence <= previous)
        {
            return;
        }

        _eventSequences[envelope.TaskId] = envelope.Sequence;
        Func<RpcEventEnvelope, ValueTask>? handler = EventReceived;
        if (handler is not null)
        {
            await handler(envelope).ConfigureAwait(false);
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (_shutdown.IsCancellationRequested)
        {
            return;
        }

        _shutdown.Cancel();
        await _stream.DisposeAsync().ConfigureAwait(false);
        try
        {
            await _readerTask.ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
        }

        _writeLock.Dispose();
        _shutdown.Dispose();
    }

    private interface IPendingCall
    {
        void Complete(JsonElement response);
        void Fail(Exception error);
    }

    private sealed class PendingCall<TResponse>(string method) : IPendingCall
        where TResponse : IProtocolValidatable
    {
        private readonly TaskCompletionSource<TResponse> _completion =
            new(TaskCreationOptions.RunContinuationsAsynchronously);

        public Task<TResponse> Task => _completion.Task;

        public void Complete(JsonElement response)
        {
            try
            {
                object envelope = ProtocolJson.DeserializeResponse(method, response);
                if (envelope is RpcErrorEnvelope error)
                {
                    _completion.TrySetException(new WorkerRpcException(error.Error));
                    return;
                }

                _completion.TrySetResult(((RpcResponseEnvelope<TResponse>)envelope).Result);
            }
            catch (Exception error)
            {
                _completion.TrySetException(error);
            }
        }

        public void Fail(Exception error) => _completion.TrySetException(error);
    }
}

public sealed class WorkerRpcException(RpcErrorBody error)
    : Exception($"WorkerHost error {ProtocolJson.GetWireValue(error.Code)}: {error.Message}")
{
    public RpcErrorBody Error { get; } = error;
}
