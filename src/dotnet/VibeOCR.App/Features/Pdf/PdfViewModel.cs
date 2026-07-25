using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Features.Pdf;

public sealed class PdfViewModel(IInferenceClient inference, IPdfFileSource files) : INotifyPropertyChanged
{
    private CancellationTokenSource? _activeRun;
    private long _generation;
    private bool _isBusy;
    private string _status = "请选择 PDF";
    private string? _sessionId;
    private string? _filePath;
    private int _pageCount;
    private int _selectedPage = -1;

    public event PropertyChangedEventHandler? PropertyChanged;
    public ObservableCollection<PdfPageViewModel> Pages { get; } = [];
    public bool IsBusy { get => _isBusy; private set => SetField(ref _isBusy, value); }
    public string Status { get => _status; private set => SetField(ref _status, value); }
    public string? SessionId { get => _sessionId; private set => SetField(ref _sessionId, value); }
    public string? FilePath { get => _filePath; private set => SetField(ref _filePath, value); }
    public int PageCount { get => _pageCount; private set => SetField(ref _pageCount, value); }
    public int SelectedPage { get => _selectedPage; set => SetField(ref _selectedPage, value); }
    public bool HasSession => _sessionId is not null;

    public async Task OpenAsync(CancellationToken ct) { string? path = await files.PickFileAsync(ct); if (path is null) { Status = "已取消选择"; return; } await OpenPathAsync(path, ct); }

    public async Task OpenPathAsync(string path, CancellationToken ct)
    {
        CancelActiveRun();
        long generation = Volatile.Read(ref _generation);
        using var run = CancellationTokenSource.CreateLinkedTokenSource(ct);
        _activeRun = run;
        IsBusy = true; Status = "正在打开";
        try
        {
            PdfSessionOpenResult result = await inference.OpenPdfSessionAsync(path, null, run.Token);
            if (generation != Volatile.Read(ref _generation)) return;
            SessionId = result.SessionId; FilePath = result.FilePath; PageCount = result.PageCount;
            Pages.Clear(); for (int i = 0; i < PageCount; i++) Pages.Add(new PdfPageViewModel { Index = i });
            Status = $"已打开 {PageCount} 页";
        }
        catch (OperationCanceledException) { if (generation == Volatile.Read(ref _generation)) Status = "已取消"; }
        catch (InferenceClientException e) { if (generation == Volatile.Read(ref _generation)) Status = LocalizeV2(e.Code); }
        catch (Exception) when (generation == Volatile.Read(ref _generation)) { Status = "打开失败"; }
        finally { if (generation == Volatile.Read(ref _generation)) IsBusy = false; }
    }

    public async Task<byte[]?> RenderThumbnailAsync(int pageIndex, CancellationToken ct)
    {
        if (SessionId is null) return null;
        try { return await inference.RenderPdfPageAsync(SessionId, pageIndex, 160, ct); }
        catch { return null; }
    }

    public async Task RotateAsync(int[] pages, int angle, CancellationToken ct)
    {
        if (SessionId is null || pages.Length == 0) { Status = "请先选中要旋转的页面"; return; }
        await MutateAsync(async token => (await inference.RotatePdfPagesAsync(SessionId!, pages, angle, token)).PageCount, "正在旋转", ct);
    }

    public async Task DeletePagesAsync(int[] pages, CancellationToken ct)
    {
        if (SessionId is null || pages.Length == 0) return;
        await MutateAsync(async token => (await inference.DeletePdfPagesAsync(SessionId!, pages, token)).PageCount, "正在删除页面", ct);
    }

    public async Task StartOcrAsync(int[] pages, bool overwrite, CancellationToken ct)
    {
        if (SessionId is null || pages.Length == 0) { Status = "请先打开 PDF"; return; }
        CancelActiveRun();
        long generation = Volatile.Read(ref _generation);
        using var run = CancellationTokenSource.CreateLinkedTokenSource(ct);
        _activeRun = run;
        IsBusy = true; Status = "正在识别";
        foreach (int idx in pages) if (idx < Pages.Count) Pages[idx].State = PdfPageState.Processing;
        string? jobId = null;
        try
        {
            var uploads = new RecognitionUpload[pages.Length];
            for (int i = 0; i < pages.Length; i++) { byte[] img = await inference.RenderPdfPageAsync(SessionId, pages[i], 1024, run.Token); uploads[i] = new RecognitionUpload($"page-{pages[i] + 1}.png", "image/png", img); }
            JobRef referral = await inference.SubmitRecognitionAsync(uploads, JobPriority.Background, run.Token);
            jobId = referral.JobId;
            JobSnapshot snap = await inference.GetJobAsync(jobId, run.Token);
            int lastSeq = snap.EventSequence;
            while (snap.State is not (JobState.Completed or JobState.CompletedWithErrors or JobState.Cancelled or JobState.Failed))
            {
                run.Token.ThrowIfCancellationRequested();
                var events = await inference.GetEventsAsync(jobId, lastSeq, run.Token);
                lastSeq = events.Count > 0 ? events[^1].Sequence : lastSeq;
                snap = await inference.GetJobAsync(jobId, run.Token);
            }
            if (generation != Volatile.Read(ref _generation)) return;
            if (snap.State is JobState.Cancelled) { foreach (int idx in pages) if (idx < Pages.Count) Pages[idx].State = PdfPageState.None; Status = "已取消"; return; }
            var results = await inference.GetResultAsync(jobId, run.Token);
            int s = 0, f = 0;
            for (int i = 0; i < pages.Length && i < results.Count; i++)
            {
                if (generation != Volatile.Read(ref _generation)) return;
                int idx = pages[i]; if (idx < 0 || idx >= Pages.Count) continue;
                var entry = results[i];
                if (!string.IsNullOrEmpty(entry.ErrorCode)) { Pages[idx].State = PdfPageState.None; f++; }
                else { Pages[idx].OcrText = ExtractText(entry); Pages[idx].State = PdfPageState.Done; s++; }
            }
            Status = $"OCR 完成：成功 {s} 页，失败 {f} 页";
        }
        catch (OperationCanceledException) { if (jobId is not null) { try { await inference.CancelAsync(jobId, CancellationToken.None); } catch { } } if (generation == Volatile.Read(ref _generation)) { foreach (int idx in pages) if (idx < Pages.Count) Pages[idx].State = PdfPageState.None; Status = "已取消"; } }
        catch (InferenceClientException e) { if (generation == Volatile.Read(ref _generation)) Status = LocalizeV2(e.Code); }
        catch (Exception) when (generation == Volatile.Read(ref _generation)) { Status = "OCR 失败"; }
        finally { if (generation == Volatile.Read(ref _generation)) IsBusy = false; }
    }

    public async Task SaveAsync(string path, CancellationToken ct)
    {
        if (SessionId is null) return;
        long generation = Volatile.Read(ref _generation);
        IsBusy = true; Status = "正在保存";
        try { string saved = await inference.SavePdfAsync(SessionId, path, ct); if (generation == Volatile.Read(ref _generation)) Status = $"已保存到 {saved}"; }
        catch (InferenceClientException e) { if (generation == Volatile.Read(ref _generation)) Status = LocalizeV2(e.Code); }
        catch (Exception) when (generation == Volatile.Read(ref _generation)) { Status = "保存失败"; }
        finally { if (generation == Volatile.Read(ref _generation)) IsBusy = false; }
    }

    public void Cancel() => CancelActiveRun();
    public void CloseSession() { CancelActiveRun(); SessionId = null; FilePath = null; PageCount = 0; Pages.Clear(); Status = "请选择 PDF"; }

    private async Task MutateAsync(Func<CancellationToken, Task<int>> action, string runningStatus, CancellationToken ct)
    {
        if (SessionId is null) return;
        long generation = Volatile.Read(ref _generation);
        IsBusy = true; Status = runningStatus;
        try { int count = await action(ct); if (generation == Volatile.Read(ref _generation)) { PageCount = count; Status = "完成"; } }
        catch (OperationCanceledException) { if (generation == Volatile.Read(ref _generation)) Status = "已取消"; }
        catch (InferenceClientException e) { if (generation == Volatile.Read(ref _generation)) Status = LocalizeV2(e.Code); }
        catch (Exception) when (generation == Volatile.Read(ref _generation)) { Status = "操作失败"; }
        finally { if (generation == Volatile.Read(ref _generation)) IsBusy = false; }
    }

    private void CancelActiveRun() { Interlocked.Increment(ref _generation); var run = Interlocked.Exchange(ref _activeRun, null); if (run is not null) { run.Cancel(); run.Dispose(); } }

    private static string ExtractText(ResultEntry entry) { if (entry.Payload.TryGetValue("text", out JsonElement el) && el.ValueKind == JsonValueKind.String) return el.GetString() ?? ""; return ""; }
    private static string LocalizeV2(HttpV2ErrorCode code) => code switch { HttpV2ErrorCode.OutOfMemory => "内存或显存不足", HttpV2ErrorCode.BackendUnavailable or HttpV2ErrorCode.TransientBackend => "Supervisor 暂不可用", HttpV2ErrorCode.Cancelled => "已取消", _ => "操作失败" };
    private void SetField<T>(ref T field, T value, [CallerMemberName] string? name = null) { if (EqualityComparer<T>.Default.Equals(field, value)) return; field = value; PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name)); }
}

public enum PdfPageState { None, Processing, Done, Failed }

public sealed class PdfPageViewModel : INotifyPropertyChanged
{
    private PdfPageState _state = PdfPageState.None;
    private string _ocrText = string.Empty;
    public event PropertyChangedEventHandler? PropertyChanged;
    public int Index { get; init; }
    public PdfPageState State { get => _state; set { if (_state != value) { _state = value; PropertyChanged?.Invoke(this, new(nameof(State))); } } }
    public string OcrText { get => _ocrText; set { if (_ocrText != value) { _ocrText = value; PropertyChanged?.Invoke(this, new(nameof(OcrText))); } } }
}

public interface IPdfFileSource { Task<string?> PickFileAsync(CancellationToken cancellationToken); }
