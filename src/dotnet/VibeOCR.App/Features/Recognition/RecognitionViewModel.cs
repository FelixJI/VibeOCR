using System.ComponentModel;
using System.Runtime.CompilerServices;
using VibeOCR.App.Inference;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Features.Recognition;

public sealed class RecognitionViewModel : INotifyPropertyChanged
{
    private readonly IInferenceClient _inference;
    private readonly InferenceJobRunner _jobs;
    private readonly IInputService _inputs;
    private CancellationTokenSource? _activeRun;
    private long _generation;
    private bool _isBusy;
    private string _resultText = string.Empty;
    private RecognizeResponse? _result;
    private RecognitionInput? _currentInput;
    private string _status = "请选择图片";

    public RecognitionViewModel(IInferenceClient inference, IInputService inputs)
    {
        _inference = inference ?? throw new ArgumentNullException(nameof(inference));
        _jobs = new InferenceJobRunner(inference);
        _inputs = inputs ?? throw new ArgumentNullException(nameof(inputs));
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public bool IsBusy { get => _isBusy; private set => SetField(ref _isBusy, value); }
    public string ResultText { get => _resultText; private set => SetField(ref _resultText, value); }
    public string Status { get => _status; private set => SetField(ref _status, value); }
    public RecognitionInput? CurrentInput { get => _currentInput; private set => SetField(ref _currentInput, value); }
    public bool HasResult => _result is not null;
    public string Pipeline { get; set; } = "OCR";
    public string? Language { get; set; }
    public RecognizeResponse? Result => _result;

    public ResultActions CreateResultActions(IResultActionPlatform platform)
    {
        var actions = new ResultActions(_inference, platform);
        if (_result is not null) actions.SetResult(_result);
        return actions;
    }

    public Task RecognizeFileAsync(CancellationToken cancellationToken) =>
        RecognizeViaSupervisorAsync(_inputs.PickFileAsync, cancellationToken);

    public Task RecognizeClipboardAsync(CancellationToken cancellationToken) =>
        RecognizeViaSupervisorAsync(_inputs.ReadClipboardAsync, cancellationToken);

    public Task RecognizeScreenshotAsync(CancellationToken cancellationToken) =>
        RecognizeViaSupervisorAsync(_inputs.CaptureScreenAsync, cancellationToken);

    public Task RecognizeDroppedFileAsync(string path, CancellationToken cancellationToken) =>
        RecognizeViaSupervisorAsync(ct => _inputs.ReadDroppedFileAsync(path, ct), cancellationToken);

    public void Cancel() => _activeRun?.Cancel();

    public async Task RecognizeViaSupervisorAsync(
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
                if (generation == Volatile.Read(ref _generation)) Status = "已取消选择";
                return;
            }

            if (generation == Volatile.Read(ref _generation))
            {
                CurrentInput = input;
                _result = null;
                ResultText = string.Empty;
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Result)));
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(HasResult)));
                Status = "正在识别";
            }

            const string clientItemKey = "recognition-input";
            InferenceJobRun job = await _jobs.RunRecognitionAsync(
                Pipeline,
                JobPriority.Interactive,
                [
                    new InferenceUploadInput(
                        clientItemKey,
                        input.DisplayName,
                        input.MediaType,
                        input.Data),
                ],
                options: null,
                run.Token);
            JobSnapshot snapshot = job.Snapshot;

            if (generation != Volatile.Read(ref _generation)) return;

            if (snapshot.State is JobState.Cancelled) { Status = "已取消"; return; }
            if (snapshot.State is JobState.Failed) { Status = "识别失败"; return; }

            ItemOutcome outcome = job.OutcomesByClientItemKey[clientItemKey];
            if (outcome.State is not ItemState.Succeeded)
            {
                Status = outcome.State is ItemState.Cancelled ? "已取消" : "识别失败";
                return;
            }

            _result = RecognitionOutcomeMapper.ToResponse(outcome, Pipeline);
            ResultText = _result.Text;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Result)));
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(HasResult)));
            Status = "识别完成";
        }
        catch (OperationCanceledException)
        {
            if (generation == Volatile.Read(ref _generation)) Status = "已取消";
        }
        catch (InferenceClientException error)
        {
            if (generation == Volatile.Read(ref _generation))
                Status = LocalizeV2(error.Code);
        }
        catch (Exception error) when (error is IOException or ObjectDisposedException)
        {
            if (generation == Volatile.Read(ref _generation))
                Status = "Supervisor 已断开，请重试";
        }
        finally
        {
            if (generation == Volatile.Read(ref _generation))
            {
                IsBusy = false;
                if (ReferenceEquals(Interlocked.CompareExchange(ref _activeRun, null, run), run))
                    run.Dispose();
            }
        }
    }

    private static string LocalizeV2(HttpV2ErrorCode code) => code switch
    {
        HttpV2ErrorCode.ValidationError => "输入图片无效",
        HttpV2ErrorCode.QuotaExceeded => "输入过大",
        HttpV2ErrorCode.Unauthorized => "Supervisor 会话无效",
        HttpV2ErrorCode.ForbiddenLoopback => "Supervisor 拒绝非本地连接",
        HttpV2ErrorCode.JobNotFound => "任务已过期",
        HttpV2ErrorCode.BackendUnavailable or HttpV2ErrorCode.TransientBackend
            => "Supervisor 暂不可用，请重试",
        HttpV2ErrorCode.Cancelled => "已取消",
        HttpV2ErrorCode.OutOfMemory => "内存或显存不足",
        HttpV2ErrorCode.SupervisorDraining => "Supervisor 正在关闭，请稍后",
        HttpV2ErrorCode.ProtocolMismatch => "Supervisor 协议不兼容",
        _ => "识别失败",
    };

    private void SetField<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
