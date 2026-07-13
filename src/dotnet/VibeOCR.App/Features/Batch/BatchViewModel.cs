using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;

namespace VibeOCR.App.Features.Batch;

public sealed class BatchViewModel(IWorkerHostClient worker, IBatchFileSource files) : INotifyPropertyChanged
{
    private readonly Dictionary<Guid, CancellationTokenSource> _itemRuns = [];
    private CancellationTokenSource? _run;
    private long _generation;
    private bool _isRunning;
    private int _completedCount;
    private int _failedCount;
    private int _concurrency = 1;

    public event PropertyChangedEventHandler? PropertyChanged;
    public ObservableCollection<BatchItemViewModel> Items { get; } = [];
    public bool IsRunning { get => _isRunning; private set => Set(ref _isRunning, value); }
    public int CompletedCount { get => _completedCount; private set => Set(ref _completedCount, value); }
    public int FailedCount { get => _failedCount; private set => Set(ref _failedCount, value); }
    public int TotalCount => Items.Count;
    public int Concurrency { get => _concurrency; set => Set(ref _concurrency, Math.Clamp(value, 1, 4)); }
    public string Progress => $"{CompletedCount + FailedCount}/{TotalCount}";

    public void AddFiles(IEnumerable<string> paths)
    {
        var known = Items.Select(item => item.Path).ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (string path in paths.Select(Path.GetFullPath)) if (known.Add(path)) Items.Add(new BatchItemViewModel(path));
        NotifyQueue();
    }

    public async Task PickFilesAsync(CancellationToken cancellationToken) => AddFiles(await files.PickFilesAsync(cancellationToken));
    public void Move(Guid id, int delta)
    {
        int from = Items.ToList().FindIndex(item => item.Id == id);
        int to = Math.Clamp(from + delta, 0, Items.Count - 1);
        if (from >= 0 && from != to) Items.Move(from, to);
    }
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
        IsRunning = true;
        using var budget = new SemaphoreSlim(Concurrency, Concurrency);
        try { await Task.WhenAll(pending.Select(item => ProcessAsync(item, generation, budget, _run.Token))); }
        finally { if (generation == Volatile.Read(ref _generation)) IsRunning = false; _run.Dispose(); _run = null; }
    }

    public void CancelItem(Guid id) { if (_itemRuns.TryGetValue(id, out CancellationTokenSource? run)) run.Cancel(); }
    public void CancelAll()
    {
        Interlocked.Increment(ref _generation);
        _run?.Cancel();
        // Mirror the Python batch queue: cancel every non-terminal item, including those
        // that were queued but never started, so the UI reflects the user's intent.
        foreach (BatchItemViewModel item in Items.Where(item => item.State is BatchItemState.Running or BatchItemState.Pending)) item.State = BatchItemState.Cancelled;
        IsRunning = false;
    }

    public void ResetTemporaryQueue() { CancelAll(); Items.Clear(); CompletedCount = 0; FailedCount = 0; NotifyQueue(); }

    public async Task<ExportOcrResponse> ExportAsync(Guid id, string outputPath, string format, bool overwrite, CancellationToken cancellationToken)
    {
        BatchItemViewModel item = Items.Single(entry => entry.Id == id);
        RecognizeResponse result = item.Result ?? throw new InvalidOperationException("The batch item has no result.");
        return await worker.CallAsync<ExportOcrRequest, ExportOcrResponse>(RpcMethods.ExportOcr, ExportRequest(result, outputPath, format, overwrite), cancellationToken);
    }

    public async Task<IReadOnlyList<ExportOcrResponse>> ExportAllAsync(string directory, string format, CancellationToken cancellationToken)
    {
        Directory.CreateDirectory(directory);
        var reserved = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var exports = new List<ExportOcrResponse>();
        foreach (BatchItemViewModel item in Items.Where(entry => entry.Result is not null))
        {
            string path = BatchCommands.UniqueOutputPath(directory, item.Path, format, reserved);
            exports.Add(await ExportAsync(item.Id, path, format, false, cancellationToken));
        }
        return exports;
    }

    private async Task ProcessAsync(BatchItemViewModel item, long generation, SemaphoreSlim budget, CancellationToken cancellationToken)
    {
        using var itemRun = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _itemRuns[item.Id] = itemRun;
        string? payloadName = null;
        try
        {
            await budget.WaitAsync(itemRun.Token);
            item.Reset(); item.State = BatchItemState.Running;
            (byte[] data, string mediaType) = await files.ReadAsync(item.Path, itemRun.Token);
            SharedPayloadRef payload = worker.CreatePayload(data, mediaType, TimeSpan.FromMinutes(5)); payloadName = payload.Name;
            RecognizeResponse result = await worker.CallAsync<RecognizeRequest, RecognizeResponse>(RpcMethods.Recognize, new RecognizeRequest { Image = payload, Pipeline = "OCR" }, itemRun.Token);
            if (generation != Volatile.Read(ref _generation)) return;
            item.Result = result; item.State = BatchItemState.Completed; CompletedCount++;
        }
        catch (OperationCanceledException) { if (generation == Volatile.Read(ref _generation)) item.State = BatchItemState.Cancelled; }
        catch (Exception error) { if (generation == Volatile.Read(ref _generation)) { item.Error = error.GetType().Name; item.State = BatchItemState.Failed; FailedCount++; } }
        finally { if (payloadName is not null) worker.ReleasePayload(payloadName); _itemRuns.Remove(item.Id); try { budget.Release(); } catch (SemaphoreFullException) { } NotifyProgress(); }
    }

    private static ExportOcrRequest ExportRequest(RecognizeResponse result, string path, string format, bool overwrite) => new()
    {
        RawText = result.RawText ?? result.Text, MarkdownText = result.MarkdownText ?? result.Text, HtmlText = result.HtmlText ?? result.Text,
        RawBlocks = result.RawBlocks ?? [], OutputPath = Path.GetFullPath(path), Format = format, Overwrite = overwrite,
    };
    private void NotifyQueue() { PropertyChanged?.Invoke(this, new(nameof(TotalCount))); NotifyProgress(); }
    private void NotifyProgress() => PropertyChanged?.Invoke(this, new(nameof(Progress)));
    private void Set<T>(ref T field, T value, [CallerMemberName] string? name = null) { if (EqualityComparer<T>.Default.Equals(field, value)) return; field = value; PropertyChanged?.Invoke(this, new(name)); if (name is nameof(CompletedCount) or nameof(FailedCount)) NotifyProgress(); }
}
