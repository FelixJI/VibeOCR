using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using System.Diagnostics;
using System.Security.Cryptography;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;
using Xunit;

namespace VibeOCR.Platform.Tests;

public sealed class WorkerHostClientTests
{
    [Fact]
    [Trait("Category", "WindowsIntegration")]
    public async Task RealPythonWorkerHandshakeAndPing()
    {
        string? python = Environment.GetEnvironmentVariable("VIBEOCR_TEST_PYTHON");
        if (string.IsNullOrWhiteSpace(python))
        {
            return;
        }

        string repository = FindRepositoryRoot();
        string pipeName = $@"\\.\pipe\VibeOCR-{Guid.NewGuid():D}";
        string token = Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(32));
        var startInfo = new ProcessStartInfo
        {
            FileName = python,
            WorkingDirectory = repository,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        startInfo.ArgumentList.Add("-m");
        startInfo.ArgumentList.Add("vibeocr.worker_host.main");
        startInfo.ArgumentList.Add("--pipe");
        startInfo.ArgumentList.Add(pipeName);
        startInfo.ArgumentList.Add("--token");
        startInfo.ArgumentList.Add(token);
        startInfo.ArgumentList.Add("--profile");
        startInfo.ArgumentList.Add("winui-dev");
        startInfo.ArgumentList.Add("--parent-pid");
        startInfo.ArgumentList.Add(Environment.ProcessId.ToString());
        startInfo.Environment["PYTHONPATH"] = Path.Combine(repository, "src");
        using Process worker = Process.Start(startInfo)
            ?? throw new InvalidOperationException("Failed to start Python WorkerHost.");
        try
        {
            string? ready = null;
            for (int attempt = 0; attempt < 20; attempt++)
            {
                string? line = await worker.StandardOutput.ReadLineAsync(
                    TestContext.Current.CancellationToken);
                if (line is null)
                {
                    break;
                }

                if (line.TrimStart().StartsWith('{'))
                {
                    ready = line;
                    break;
                }
            }

            Assert.NotNull(ready);
            Assert.Equal(
                "worker.ready",
                JsonDocument.Parse(ready).RootElement.GetProperty("event").GetString());

            await using WorkerHostClient client = await WorkerHostClient.ConnectAsync(
                pipeName,
                token,
                TimeSpan.FromSeconds(10),
                TimeSpan.FromSeconds(10),
                TestContext.Current.CancellationToken);
            HandshakeResponse handshake = await client.CallAsync<HandshakeRequest, HandshakeResponse>(
                RpcMethods.Handshake,
                new HandshakeRequest
                {
                    AppVersion = "0.5.0",
                    ProtocolVersion = 1,
                    MaxMessageBytes = FrameCodec.DefaultMaxFrameBytes,
                    MaxSharedPayloadBytes = 256 << 20,
                },
                TestContext.Current.CancellationToken);
            PingResponse ping = await client.CallAsync<PingRequest, PingResponse>(
                RpcMethods.Ping,
                new PingRequest { Nonce = "integration" },
                TestContext.Current.CancellationToken);

            Assert.Equal(1, handshake.ProtocolVersion);
            Assert.Equal("integration", ping.Nonce);
            Assert.False(worker.HasExited);
        }
        finally
        {
            if (!worker.HasExited)
            {
                worker.Kill(entireProcessTree: true);
            }
        }
    }

    [Fact]
    public async Task FrameCodecHandlesFragmentedAndAdjacentFrames()
    {
        await using var stream = new MemoryStream();
        CancellationToken testCancellation = TestContext.Current.CancellationToken;
        await FrameCodec.WriteAsync(stream, "one"u8.ToArray(), testCancellation);
        await FrameCodec.WriteAsync(stream, "two"u8.ToArray(), testCancellation);
        stream.Position = 0;
        await using var fragmented = new ChunkedReadStream(stream, 2);

        Assert.Equal("one", Encoding.UTF8.GetString(await FrameCodec.ReadAsync(fragmented, testCancellation)));
        Assert.Equal("two", Encoding.UTF8.GetString(await FrameCodec.ReadAsync(fragmented, testCancellation)));
    }

    [Fact]
    public async Task CallCorrelatesResponseAcrossFragmentedDuplexStream()
    {
        (Stream clientStream, Stream workerStream) = DuplexStream.CreatePair(3);
        await using var client = new WorkerHostClient(clientStream, TimeSpan.FromSeconds(5));
        Task worker = Task.Run(async () =>
        {
            JsonElement request = JsonDocument.Parse(
                await FrameCodec.ReadAsync(workerStream, TestContext.Current.CancellationToken)).RootElement.Clone();
            string response = JsonSerializer.Serialize(new
            {
                protocol_version = 1,
                request_id = request.GetProperty("request_id").GetString(),
                task_id = request.GetProperty("task_id").GetString(),
                result = new { nonce = "pong" },
            });
            await FrameCodec.WriteAsync(
                workerStream,
                Encoding.UTF8.GetBytes(response),
                TestContext.Current.CancellationToken);
        }, TestContext.Current.CancellationToken);

        PingResponse response = await client.CallAsync<PingRequest, PingResponse>(
            RpcMethods.Ping,
            new PingRequest { Nonce = "pong" },
            TestContext.Current.CancellationToken);

        Assert.Equal("pong", response.Nonce);
        await worker;
    }

    [Fact]
    public async Task CancellationSendsProtocolCancelForOriginalTask()
    {
        (Stream clientStream, Stream workerStream) = DuplexStream.CreatePair(5);
        await using var client = new WorkerHostClient(clientStream, TimeSpan.FromSeconds(5));
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(
            TestContext.Current.CancellationToken);
        Task<PingResponse> call = client.CallAsync<PingRequest, PingResponse>(
            RpcMethods.Ping,
            new PingRequest { Nonce = "wait" },
            cancellation.Token);

        JsonElement original = JsonDocument.Parse(
            await FrameCodec.ReadAsync(workerStream, TestContext.Current.CancellationToken)).RootElement.Clone();
        cancellation.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => call);
        JsonElement cancel = JsonDocument.Parse(
            await FrameCodec.ReadAsync(workerStream, TestContext.Current.CancellationToken)).RootElement.Clone();

        Assert.Equal(RpcMethods.Cancel, cancel.GetProperty("method").GetString());
        Assert.Equal(
            original.GetProperty("task_id").GetString(),
            cancel.GetProperty("payload").GetProperty("task_id").GetString());
    }

    [Fact]
    public async Task DeadlineSendsCancelAndRaisesTimeout()
    {
        (Stream clientStream, Stream workerStream) = DuplexStream.CreatePair(4);
        await using var client = new WorkerHostClient(clientStream, TimeSpan.FromMilliseconds(100));
        Task<PingResponse> call = client.CallAsync<PingRequest, PingResponse>(
            RpcMethods.Ping,
            new PingRequest { Nonce = "timeout" },
            TestContext.Current.CancellationToken);

        JsonElement original = JsonDocument.Parse(
            await FrameCodec.ReadAsync(workerStream, TestContext.Current.CancellationToken)).RootElement.Clone();
        await Assert.ThrowsAsync<TimeoutException>(() => call);
        JsonElement cancel = JsonDocument.Parse(
            await FrameCodec.ReadAsync(workerStream, TestContext.Current.CancellationToken)).RootElement.Clone();

        Assert.Equal(RpcMethods.Cancel, cancel.GetProperty("method").GetString());
        Assert.Equal(
            original.GetProperty("task_id").GetString(),
            cancel.GetProperty("payload").GetProperty("task_id").GetString());
    }

    [Fact]
    public async Task StaleEventsAreDroppedBySequence()
    {
        (Stream clientStream, Stream workerStream) = DuplexStream.CreatePair(7);
        await using var client = new WorkerHostClient(clientStream, TimeSpan.FromSeconds(5));
        int received = 0;
        client.EventReceived += _ =>
        {
            Interlocked.Increment(ref received);
            return ValueTask.CompletedTask;
        };
        Task<PingResponse> call = client.CallAsync<PingRequest, PingResponse>(
            RpcMethods.Ping,
            new PingRequest { Nonce = "events" },
            TestContext.Current.CancellationToken);
        JsonElement request = JsonDocument.Parse(
            await FrameCodec.ReadAsync(workerStream, TestContext.Current.CancellationToken)).RootElement.Clone();
        string taskId = request.GetProperty("task_id").GetString()!;

        await WriteJsonAsync(workerStream, new
        {
            protocol_version = 1,
            task_id = taskId,
            @event = "task.progress",
            sequence = 1,
            payload = new { current = 1 },
        });
        await WriteJsonAsync(workerStream, new
        {
            protocol_version = 1,
            task_id = taskId,
            @event = "task.progress",
            sequence = 0,
            payload = new { current = 0 },
        });
        await WriteJsonAsync(workerStream, new
        {
            protocol_version = 1,
            request_id = request.GetProperty("request_id").GetString(),
            task_id = taskId,
            result = new { nonce = "events" },
        });

        Assert.Equal("events", (await call).Nonce);
        Assert.Equal(1, Volatile.Read(ref received));
    }

    [Fact]
    public async Task DisconnectFailsPendingCall()
    {
        (Stream clientStream, Stream workerStream) = DuplexStream.CreatePair(8);
        await using var client = new WorkerHostClient(clientStream, TimeSpan.FromSeconds(5));
        Task<PingResponse> call = client.CallAsync<PingRequest, PingResponse>(
            RpcMethods.Ping,
            new PingRequest { Nonce = "disconnect" },
            TestContext.Current.CancellationToken);
        _ = await FrameCodec.ReadAsync(workerStream, TestContext.Current.CancellationToken);

        await workerStream.DisposeAsync();

        await Assert.ThrowsAnyAsync<Exception>(() => call);
    }

    [Fact]
    public async Task SharedPayloadRoundTripsAndReleasesOwnerHandle()
    {
        await using var payloads = new SharedPayloadClient(Guid.NewGuid());
        byte[] expected = "shared-payload"u8.ToArray();
        SharedPayloadRef reference = payloads.Create(expected, "application/octet-stream", TimeSpan.FromMinutes(1));

        Assert.Equal(expected, payloads.Read(reference));
        Assert.True(payloads.Release(reference.Name));
        Assert.False(payloads.Release(reference.Name));
    }

    private static Task WriteJsonAsync(Stream stream, object value) =>
        FrameCodec.WriteAsync(
            stream,
            JsonSerializer.SerializeToUtf8Bytes(value),
            TestContext.Current.CancellationToken).AsTask();

    private static string FindRepositoryRoot()
    {
        foreach (string? seed in new[]
                 {
                     Environment.GetEnvironmentVariable("VIBEOCR_REPOSITORY_ROOT"),
                     Directory.GetCurrentDirectory(),
                     AppContext.BaseDirectory,
                 })
        {
            DirectoryInfo? directory = string.IsNullOrWhiteSpace(seed) ? null : new(seed);
            while (directory is not null)
            {
                if (File.Exists(Path.Combine(directory.FullName, "pyproject.toml")))
                {
                    return directory.FullName;
                }

                directory = directory.Parent;
            }
        }

        throw new DirectoryNotFoundException("Could not locate repository root.");
    }

    private sealed class ChunkedReadStream(Stream inner, int maxChunk) : Stream
    {
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override int Read(byte[] buffer, int offset, int count) =>
            inner.Read(buffer, offset, Math.Min(count, maxChunk));
        public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default) =>
            inner.ReadAsync(buffer[..Math.Min(buffer.Length, maxChunk)], cancellationToken);
        public override void Flush() => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        protected override void Dispose(bool disposing) { if (disposing) inner.Dispose(); base.Dispose(disposing); }
    }

    private sealed class DuplexStream(ChannelReader<byte[]> incoming, ChannelWriter<byte[]> outgoing, int maxChunk) : Stream
    {
        private byte[]? _current;
        private int _offset;

        public static (Stream, Stream) CreatePair(int maxChunk)
        {
            Channel<byte[]> left = Channel.CreateUnbounded<byte[]>();
            Channel<byte[]> right = Channel.CreateUnbounded<byte[]>();
            return (
                new DuplexStream(left.Reader, right.Writer, maxChunk),
                new DuplexStream(right.Reader, left.Writer, maxChunk));
        }

        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => true;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }

        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            if (_current is null || _offset == _current.Length)
            {
                _current = await incoming.ReadAsync(cancellationToken);
                _offset = 0;
            }

            int count = Math.Min(Math.Min(buffer.Length, maxChunk), _current.Length - _offset);
            _current.AsMemory(_offset, count).CopyTo(buffer);
            _offset += count;
            return count;
        }

        public override ValueTask WriteAsync(ReadOnlyMemory<byte> buffer, CancellationToken cancellationToken = default) =>
            outgoing.WriteAsync(buffer.ToArray(), cancellationToken);
        public override void Flush() { }
        public override Task FlushAsync(CancellationToken cancellationToken) => Task.CompletedTask;
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                outgoing.TryComplete();
            }

            base.Dispose(disposing);
        }
    }
}
