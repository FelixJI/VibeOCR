using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using VibeOCR.App.Inference;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Features.Batch;

public sealed class BatchViewModel(IInferenceClient inference, IBatchFileSource files) : INotifyPropertyChanged
{
    private readonly InferenceJobRunner _jobs = new(inference);
    private readonly object _counterLock = new();
    private CancellationTokenSource? _run;
    private long _generation;
    private bool _isRunning;
    private int _completedCount;
    private int _failedCount;

    public event PropertyChangedEventHandler? PropertyChanged;
    public ObservableCollection<BatchItemViewModel> Items { get; } = [];
    public bool IsRunning { get => _isRunning; private set => Set(ref _isRunning, value); }
    public int CompletedCount { get => _completedCount; private set => Set(ref _completedCount, value); }
    public int FailedCount { get => _failedCount; private set => Set(ref _failedCount, value); }
    public int TotalCount => Items.Count;
    public int Concurrency { get; set; } = 1;
    public string Progress => $"{CompletedCount + FailedCount}/{TotalCount}";

    public void AddFiles(IEnumerable<string> paths)
    {
        var known = Items.Select(item => item.Path).ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (string path in paths.Select(Path.GetFullPath)) if (known.Add(path)) Items.Add(new BatchItemViewModel(path));
        NotifyQueue();
    }

    public async Task PickFilesAsync(CancellationToken cancellationToken) => AddFiles(await files.PickFilesAsync(cancellationToken));
    public void Move(Guid id, int delta) { int from = Items.ToList().FindIndex(item => item.Id == id); int to = Math.Clamp(from + delta, 0, Items.Count - 1); if (from >= 0 && from != to) Items.Move(from, to); }
    public void Remove(Guid id) { BatchItemViewModel? item = Items.FirstOrDefault(entry => entry.Id == id); if (item is not null && item.State != BatchItemState.Running) Items.Remove(item); NotifyQueue(); }

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        if (IsRunning) throw new InvalidOperationException("A batch is already running.");
        BatchItemViewModel[] pending = Items.Where(item => item.State is BatchItemState.Pending or BatchItemState.Failed or BatchItemState.Cancelled).ToArray();
        if (pending.Length == 0) return;
        long generation = Interlocked.Increment(ref _generation);
        _run = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        CompletedCount = Items.Count(item => item.State == BatchItemState.Completed);
        FailedCount = 0;
        if (generation == Volatile.Read(ref _generation)) IsRunning = true;

        foreach (BatchItemViewModel item in pending) { if (generation == Volatile.Read(ref _generation)) { item.Reset(); item.State = BatchItemState.Running; } }

        try
        {
            var inputs = new InferenceUploadInput[pending.Length];
            for (int i = 0; i < pending.Length; i++)
            {
                (byte[] data, string mediaType) = await files.ReadAsync(pending[i].Path, _run.Token);
                inputs[i] = new InferenceUploadInput(
                    pending[i].Id.ToString("N"),
                    pending[i].Name,
                    mediaType,
                    data);
            }

            InferenceJobRun job = await _jobs.RunRecognitionAsync(
                "OCR",
                JobPriority.Background,
                inputs,
                options: null,
                _run.Token);
            JobSnapshot snapshot = job.Snapshot;

            if (generation != Volatile.Read(ref _generation)) return;

            if (snapshot.State is JobState.Cancelled)
            {
                foreach (BatchItemViewModel item in pending) if (item.State is BatchItemState.Running) item.State = BatchItemState.Cancelled;
                NotifyProgress();
                return;
            }

            foreach (BatchItemViewModel item in pending)
            {
                if (generation != Volatile.Read(ref _generation)) return;
                ItemOutcome outcome = job.OutcomesByClientItemKey[item.Id.ToString("N")];
                if (outcome.State is ItemState.Succeeded)
                {
                    item.Result = RecognitionOutcomeMapper.ToResponse(outcome, "OCR");
                    item.State = BatchItemState.Completed;
                    IncrementCompleted();
                }
                else if (outcome.State is ItemState.Cancelled)
                {
                    item.State = BatchItemState.Cancelled;
                }
                else
                {
                    item.Error = outcome.ErrorCode ?? "INFERENCE_FAILED";
                    item.State = BatchItemState.Failed;
                    IncrementFailed();
                }
            }
            NotifyProgress();
        }
        catch (OperationCanceledException)
        {
            if (generation == Volatile.Read(ref _generation)) foreach (BatchItemViewModel item in pending) if (item.State is BatchItemState.Running) item.State = BatchItemState.Cancelled;
        }
        catch (InferenceClientException error)
        {
            if (generation == Volatile.Read(ref _generation)) foreach (BatchItemViewModel item in pending) if (item.State is BatchItemState.Running) { item.Error = error.Code.ToString(); item.State = BatchItemState.Failed; IncrementFailed(); }
        }
        catch (Exception error) when (error is IOException)
        {
            if (generation == Volatile.Read(ref _generation)) foreach (BatchItemViewModel item in pending) if (item.State is BatchItemState.Running) { item.Error = error.GetType().Name; item.State = BatchItemState.Failed; IncrementFailed(); }
        }
        finally
        {
            if (generation == Volatile.Read(ref _generation)) { IsRunning = false; if (ReferenceEquals(Interlocked.CompareExchange(ref _run, null, _run), _run)) _run?.Dispose(); }
        }
    }

    public void CancelAll() { Interlocked.Increment(ref _generation); _run?.Cancel(); foreach (BatchItemViewModel item in Items.Where(item => item.State is BatchItemState.Running or BatchItemState.Pending)) item.State = BatchItemState.Cancelled; IsRunning = false; }
    public void ResetTemporaryQueue() { CancelAll(); Items.Clear(); CompletedCount = 0; FailedCount = 0; NotifyQueue(); }

    public async Task<ExportResult> ExportAsync(Guid id, string outputPath, string format, bool overwrite, CancellationToken ct)
    {
        BatchItemViewModel item = Items.Single(entry => entry.Id == id);
        if (item.Result is null) throw new InvalidOperationException("The batch item has no result.");
        return await inference.ExportAsync(new ExportRequest(item.Result.RawText ?? item.Result.Text, item.Result.MarkdownText ?? item.Result.Text, item.Result.HtmlText ?? item.Result.Text, outputPath, format, overwrite), ct);
    }

    public async Task<IReadOnlyList<ExportResult>> ExportAllAsync(string directory, string format, CancellationToken ct)
    {
        Directory.CreateDirectory(directory);
        var reserved = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var exports = new List<ExportResult>();
        foreach (BatchItemViewModel item in Items.Where(entry => entry.Result is not null))
        {
            string path = BatchCommands.UniqueOutputPath(directory, item.Path, format, reserved);
            exports.Add(await ExportAsync(item.Id, path, format, false, ct));
        }
        return exports;
    }

    private void NotifyQueue() { PropertyChanged?.Invoke(this, new(nameof(TotalCount))); NotifyProgress(); }
    private void NotifyProgress() => PropertyChanged?.Invoke(this, new(nameof(Progress)));
    private void IncrementCompleted() { lock (_counterLock) _completedCount++; PropertyChanged?.Invoke(this, new(nameof(CompletedCount))); NotifyProgress(); }
    private void IncrementFailed() { lock (_counterLock) _failedCount++; PropertyChanged?.Invoke(this, new(nameof(FailedCount))); NotifyProgress(); }
    private void Set<T>(ref T field, T value, [CallerMemberName] string? name = null) { if (EqualityComparer<T>.Default.Equals(field, value)) return; field = value; PropertyChanged?.Invoke(this, new(name)); if (name is nameof(CompletedCount) or nameof(FailedCount)) NotifyProgress(); }
}
