using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;

namespace VibeOCR.App.Features.Recognition;

public sealed class RecognitionViewModel : INotifyPropertyChanged
{
    private readonly IWorkerHostClient _worker;
    private readonly IInputService _inputs;
    private CancellationTokenSource? _activeRun;
    private long _generation;
    private bool _isBusy;
    private string _resultText = string.Empty;
    private RecognizeResponse? _result;
    private RecognitionInput? _currentInput;
    private string _status = "请选择图片";

    public RecognitionViewModel(IWorkerHostClient worker, IInputService inputs)
    {
        _worker = worker ?? throw new ArgumentNullException(nameof(worker));
        _inputs = inputs ?? throw new ArgumentNullException(nameof(inputs));
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public bool IsBusy
    {
        get => _isBusy;
        private set => SetField(ref _isBusy, value);
    }

    public string ResultText
    {
        get => _resultText;
        private set => SetField(ref _resultText, value);
    }

    public string Status
    {
        get => _status;
        private set => SetField(ref _status, value);
    }

    public RecognitionInput? CurrentInput
    {
        get => _currentInput;
        private set => SetField(ref _currentInput, value);
    }

    public bool HasResult => _result is not null;

    public string Pipeline { get; set; } = "OCR";

    public string? Language { get; set; }
    public RecognizeResponse? Result => _result;

    public ResultActions CreateResultActions(IResultActionPlatform platform)
    {
        var actions = new ResultActions(_worker, platform);
        if (_result is not null) actions.SetResult(_result);
        return actions;
    }

    public Task RecognizeFileAsync(CancellationToken cancellationToken) =>
        StartAsync(_inputs.PickFileAsync, cancellationToken);

    public Task RecognizeClipboardAsync(CancellationToken cancellationToken) =>
        StartAsync(_inputs.ReadClipboardAsync, cancellationToken);

    public Task RecognizeScreenshotAsync(CancellationToken cancellationToken) =>
        StartAsync(_inputs.CaptureScreenAsync, cancellationToken);

    public Task RecognizeDroppedFileAsync(string path, CancellationToken cancellationToken) =>
        StartAsync(ct => _inputs.ReadDroppedFileAsync(path, ct), cancellationToken);

    public void Cancel() => _activeRun?.Cancel();

    private async Task StartAsync(
        Func<CancellationToken, Task<RecognitionInput?>> loadInput,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(loadInput);
        long generation = Interlocked.Increment(ref _generation);
        CancellationTokenSource? previous = Interlocked.Exchange(
            ref _activeRun,
            CancellationTokenSource.CreateLinkedTokenSource(cancellationToken));
        previous?.Cancel();
        previous?.Dispose();
        CancellationTokenSource run = _activeRun;
        string? payloadName = null;
        if (generation == Volatile.Read(ref _generation))
        {
            IsBusy = true;
            Status = "正在读取输入";
        }

        try
        {
            RecognitionInput? input = await loadInput(run.Token);
            if (input is null)
            {
                if (generation == Volatile.Read(ref _generation))
                {
                    Status = "已取消选择";
                }

                return;
            }

            if (generation == Volatile.Read(ref _generation))
            {
                CurrentInput = input;
                _result = null;
                ResultText = string.Empty;
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Result)));
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(HasResult)));
            }

            SharedPayloadRef payload = _worker.CreatePayload(
                input.Data,
                input.MediaType,
                TimeSpan.FromMinutes(5));
            payloadName = payload.Name;
            if (generation == Volatile.Read(ref _generation))
            {
                Status = "正在识别";
            }

            RecognizeResponse response = await _worker.CallAsync<RecognizeRequest, RecognizeResponse>(
                RpcMethods.Recognize,
                new RecognizeRequest
                {
                    Image = payload,
                    Pipeline = Pipeline,
                    Language = Language,
                },
                run.Token);
            if (generation == Volatile.Read(ref _generation))
            {
                _result = response;
                ResultText = response.Text;
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Result)));
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(HasResult)));
                Status = "识别完成";
            }
        }
        catch (OperationCanceledException)
        {
            if (generation == Volatile.Read(ref _generation))
            {
                Status = "已取消";
            }
        }
        catch (WorkerRpcException error)
        {
            if (generation == Volatile.Read(ref _generation))
            {
                Status = Localize(error.Error.Code);
            }
        }
        catch (Exception error) when (
            error is InvalidDataException or UnauthorizedAccessException or COMException)
        {
            if (generation == Volatile.Read(ref _generation))
            {
                Status = "无法读取输入图片";
            }
        }
        catch (Exception error) when (error is IOException or ObjectDisposedException)
        {
            if (generation == Volatile.Read(ref _generation))
            {
                Status = "Worker 已断开，请重试";
            }
        }
        finally
        {
            if (payloadName is not null)
            {
                _worker.ReleasePayload(payloadName);
            }

            if (generation == Volatile.Read(ref _generation))
            {
                IsBusy = false;
                if (ReferenceEquals(Interlocked.CompareExchange(ref _activeRun, null, run), run))
                {
                    run.Dispose();
                }
            }
        }
    }

    private static string Localize(ErrorCode code) => code switch
    {
        ErrorCode.InvalidRequest => "输入图片无效",
        ErrorCode.DependencyMissing => "识别依赖尚未安装",
        ErrorCode.WorkerUnavailable => "Worker 暂不可用，请重试",
        ErrorCode.TaskCancelled => "已取消",
        ErrorCode.TaskTimeout => "识别超时，请重试",
        ErrorCode.ProtocolMismatch => "Worker 协议不兼容",
        ErrorCode.ResourceExhausted => "内存或显存不足",
        _ => "识别失败",
    };

    private void SetField<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return;
        }

        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}

public sealed class DeferredWorkerHostClient : IWorkerHostClient, IAsyncDisposable
{
    private readonly SharedPayloadClient _payloads = new(Guid.NewGuid());
    private IWorkerHostClient? _inner;
    private Func<CancellationToken, Task<IWorkerHostClient>>? _recover;
    private int _recoveryUsed;

    public bool IsAttached => Volatile.Read(ref _inner) is not null;

    public void Attach(IWorkerHostClient client) =>
        Interlocked.Exchange(ref _inner, client ?? throw new ArgumentNullException(nameof(client)));

    public void Detach(IWorkerHostClient client) =>
        Interlocked.CompareExchange(ref _inner, null, client);

    public void ConfigureRecovery(Func<CancellationToken, Task<IWorkerHostClient>> recover) =>
        _recover = recover ?? throw new ArgumentNullException(nameof(recover));

    public SharedPayloadRef CreatePayload(
        ReadOnlySpan<byte> data,
        string mediaType,
        TimeSpan ttl) => _payloads.Create(data, mediaType, ttl);

    public bool ReleasePayload(string name) => _payloads.Release(name);

    public byte[] ReadPayload(SharedPayloadRef reference, TimeSpan timeout, CancellationToken cancellationToken)
        => _payloads.Read(reference);

    public async Task<TResponse> CallAsync<TRequest, TResponse>(
        string method,
        TRequest request,
        CancellationToken cancellationToken)
        where TRequest : IProtocolValidatable
        where TResponse : IProtocolValidatable
    {
        IWorkerHostClient current = Current();
        try
        {
            return await current.CallAsync<TRequest, TResponse>(
                method,
                request,
                cancellationToken);
        }
        catch (Exception error) when (CanRecover(method, error, cancellationToken))
        {
            Func<CancellationToken, Task<IWorkerHostClient>> recover = _recover!;
            IWorkerHostClient replacement = await recover(cancellationToken);
            Attach(replacement);
            return await replacement.CallAsync<TRequest, TResponse>(
                method,
                request,
                cancellationToken);
        }
    }

    private bool CanRecover(string method, Exception error, CancellationToken cancellationToken)
    {
        bool eligible = error is IOException or ObjectDisposedException ||
            error is WorkerRpcException rpc &&
            rpc.Error.Code == ErrorCode.WorkerUnavailable &&
            rpc.Error.Retryable;
        if (!eligible || method != RpcMethods.Recognize || cancellationToken.IsCancellationRequested ||
            _recover is null)
        {
            return false;
        }

        return Interlocked.CompareExchange(ref _recoveryUsed, 1, 0) == 0;
    }

    private IWorkerHostClient Current() =>
        Volatile.Read(ref _inner) ?? throw new IOException("WorkerHost is not connected.");

    public ValueTask DisposeAsync() => _payloads.DisposeAsync();
}
