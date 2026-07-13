using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using VibeOCR.Contracts;
using VibeOCR.Platform.Worker;

namespace VibeOCR.App.Features.Pdf;

/// <summary>
/// View model for the PDF tab: opens a session, renders thumbnails/previews,
/// rotates/deletes pages, runs durable batch OCR, deletes text layers, and
/// saves. All heavy work goes through the Python WorkerHost; this view model
/// only projects session state and enforces command mutual exclusion.
/// </summary>
public sealed class PdfViewModel(IWorkerHostClient worker, IPdfFileSource files) : INotifyPropertyChanged
{
    private readonly IWorkerHostClient _worker = worker ?? throw new ArgumentNullException(nameof(worker));
    private readonly IPdfFileSource _files = files ?? throw new ArgumentNullException(nameof(files));
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

    /// <summary>Open a PDF file and populate the page grid.</summary>
    public async Task OpenAsync(CancellationToken cancellationToken)
    {
        string? path = await _files.PickFileAsync(cancellationToken);
        if (path is null)
        {
            Status = "已取消选择";
            return;
        }
        await OpenPathAsync(path, cancellationToken);
    }

    public async Task OpenPathAsync(string path, CancellationToken cancellationToken)
    {
        CancelActiveRun();
        long generation = Volatile.Read(ref _generation);
        using var run = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _activeRun = run;
        IsBusy = true;
        Status = "正在打开";
        try
        {
            OpenPdfResponse response = await _worker.CallAsync<OpenPdfRequest, OpenPdfResponse>(
                RpcMethods.OpenPdf,
                new OpenPdfRequest { FilePath = path },
                run.Token);
            if (generation != Volatile.Read(ref _generation)) return;
            SessionId = response.SessionId;
            FilePath = response.FilePath;
            PageCount = response.PageCount;
            Pages.Clear();
            for (int i = 0; i < response.PageCount; i++)
            {
                Pages.Add(new PdfPageViewModel { Index = i });
            }
            Status = $"已打开 {response.PageCount} 页";
        }
        catch (OperationCanceledException)
        {
            if (generation == Volatile.Read(ref _generation)) Status = "已取消";
        }
        catch (WorkerRpcException error)
        {
            if (generation == Volatile.Read(ref _generation))
                Status = Localize(error.Error.Code);
        }
        catch (Exception) when (generation == Volatile.Read(ref _generation))
        {
            Status = "Worker 已断开，请重试";
        }
        finally
        {
            if (generation == Volatile.Read(ref _generation))
            {
                IsBusy = false;
                ClearActiveRun(run);
            }
        }
    }

    /// <summary>Render a thumbnail for the given page index.</summary>
    public async Task<byte[]?> RenderThumbnailAsync(int pageIndex, CancellationToken cancellationToken)
    {
        if (SessionId is null) return null;
        try
        {
            RenderPdfPageResponse response = await _worker.CallAsync<RenderPdfPageRequest, RenderPdfPageResponse>(
                RpcMethods.RenderPdfPage,
                new RenderPdfPageRequest { SessionId = SessionId, PageIndex = pageIndex, Size = 160 },
                cancellationToken);
            return _worker.ReadPayload(response.Image, TimeSpan.FromSeconds(30), cancellationToken);
        }
        catch
        {
            return null;
        }
    }

    /// <summary>Rotate selected (or all) pages. angle is 90, -90, 180 or 270.</summary>
    public async Task RotateAsync(int[] pageIndices, int angle, CancellationToken cancellationToken)
    {
        if (SessionId is null || pageIndices.Length == 0)
        {
            Status = "请先选中要旋转的页面";
            return;
        }
        await MutateAsync(async ct =>
        {
            RotatePdfResponse response = await _worker.CallAsync<RotatePdfRequest, RotatePdfResponse>(
                RpcMethods.RotatePdf,
                new RotatePdfRequest { SessionId = SessionId!, PageIndices = pageIndices, Angle = angle },
                ct);
            return response.PageCount;
        }, "正在旋转", cancellationToken);
    }

    /// <summary>Delete pages by index.</summary>
    public async Task DeletePagesAsync(int[] pageIndices, CancellationToken cancellationToken)
    {
        if (SessionId is null || pageIndices.Length == 0) return;
        await MutateAsync(async ct =>
        {
            DeletePdfPagesResponse response = await _worker.CallAsync<DeletePdfPagesRequest, DeletePdfPagesResponse>(
                RpcMethods.DeletePdfPages,
                new DeletePdfPagesRequest { SessionId = SessionId!, PageIndices = pageIndices },
                ct);
            return response.PageCount;
        }, "正在删除页面", cancellationToken);
    }

    /// <summary>Run durable batch OCR over the given pages.</summary>
    public async Task StartOcrAsync(int[] pageIndices, bool overwrite, CancellationToken cancellationToken)
    {
        if (SessionId is null || FilePath is null || pageIndices.Length == 0)
        {
            Status = "请先打开 PDF";
            return;
        }
        CancelActiveRun();
        long generation = Volatile.Read(ref _generation);
        using var run = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _activeRun = run;
        IsBusy = true;
        Status = "正在识别";
        foreach (int idx in pageIndices)
        {
            if (idx < Pages.Count) Pages[idx].State = PdfPageState.Processing;
        }
        try
        {
            StartPdfOcrResponse response = await _worker.CallAsync<StartPdfOcrRequest, StartPdfOcrResponse>(
                RpcMethods.StartPdfOcr,
                new StartPdfOcrRequest
                {
                    SessionId = SessionId,
                    FilePath = FilePath,
                    PageIndices = pageIndices,
                    Overwrite = overwrite,
                },
                run.Token);
            if (generation != Volatile.Read(ref _generation)) return;
            foreach (int idx in pageIndices)
            {
                if (idx < Pages.Count) Pages[idx].State = PdfPageState.Done;
            }
            string errors = response.WriteErrors is { Length: > 0 }
                ? $"（写层错误：{string.Join("; ", response.WriteErrors)}）"
                : string.Empty;
            Status = $"OCR 完成：成功 {response.Completed} 页，失败 {response.Failed} 页{errors}";
        }
        catch (OperationCanceledException)
        {
            if (generation == Volatile.Read(ref _generation)) Status = "已取消";
        }
        catch (WorkerRpcException error)
        {
            if (generation == Volatile.Read(ref _generation))
                Status = Localize(error.Error.Code);
        }
        catch (Exception) when (generation == Volatile.Read(ref _generation))
        {
            Status = "Worker 已断开，请重试";
        }
        finally
        {
            if (generation == Volatile.Read(ref _generation))
            {
                IsBusy = false;
                ClearActiveRun(run);
            }
        }
    }

    /// <summary>Delete text layers page by page (streaming).</summary>
    public async Task DeleteTextLayersAsync(int[] pageIndices, CancellationToken cancellationToken)
    {
        if (SessionId is null || pageIndices.Length == 0) return;
        await MutateAsync(async ct =>
        {
            DeletePdfTextLayersResponse response = await _worker.CallAsync<DeletePdfTextLayersRequest, DeletePdfTextLayersResponse>(
                RpcMethods.DeletePdfTextLayers,
                new DeletePdfTextLayersRequest { SessionId = SessionId!, PageIndices = pageIndices },
                ct);
            return response.DeletedCount;
        }, "正在删除文字层", cancellationToken, expectedPageCount: null);
    }

    /// <summary>Save in place (outputPath null) or save-as.</summary>
    public async Task SaveAsync(string? outputPath, CancellationToken cancellationToken)
    {
        if (SessionId is null) return;
        long generation = Interlocked.Read(ref _generation);
        IsBusy = true;
        Status = outputPath is null ? "正在保存" : "正在另存";
        try
        {
            SavePdfResponse response = await _worker.CallAsync<SavePdfRequest, SavePdfResponse>(
                RpcMethods.SavePdf,
                new SavePdfRequest { SessionId = SessionId, OutputPath = outputPath },
                cancellationToken);
            if (generation == Volatile.Read(ref _generation))
                Status = $"已保存到 {response.SavedPath}";
        }
        catch (WorkerRpcException error)
        {
            if (generation == Volatile.Read(ref _generation))
                Status = Localize(error.Error.Code);
        }
        catch (Exception) when (generation == Volatile.Read(ref _generation))
        {
            Status = "保存失败";
        }
        finally
        {
            if (generation == Volatile.Read(ref _generation)) IsBusy = false;
        }
    }

    public void Cancel() => CancelActiveRun();

    public void CloseSession()
    {
        CancelActiveRun();
        SessionId = null;
        FilePath = null;
        PageCount = 0;
        Pages.Clear();
        Status = "请选择 PDF";
    }

    private async Task MutateAsync(
        Func<CancellationToken, Task<int>> action,
        string runningStatus,
        CancellationToken cancellationToken,
        int? expectedPageCount = null)
    {
        if (SessionId is null) return;
        long generation = Interlocked.Read(ref _generation);
        IsBusy = true;
        Status = runningStatus;
        try
        {
            int pageCount = await action(cancellationToken);
            if (generation == Volatile.Read(ref _generation))
            {
                PageCount = pageCount;
                Status = "完成";
            }
        }
        catch (OperationCanceledException)
        {
            if (generation == Volatile.Read(ref _generation)) Status = "已取消";
        }
        catch (WorkerRpcException error)
        {
            if (generation == Volatile.Read(ref _generation))
                Status = Localize(error.Error.Code);
        }
        catch (Exception) when (generation == Volatile.Read(ref _generation))
        {
            Status = "操作失败";
        }
        finally
        {
            if (generation == Volatile.Read(ref _generation)) IsBusy = false;
        }
    }

    private void CancelActiveRun()
    {
        Interlocked.Increment(ref _generation);
        CancellationTokenSource? run = Interlocked.Exchange(ref _activeRun, null);
        if (run is not null)
        {
            run.Cancel();
            run.Dispose();
        }
    }

    private void ClearActiveRun(CancellationTokenSource expected)
    {
        if (ReferenceEquals(Interlocked.CompareExchange(ref _activeRun, null, expected), expected))
        {
            // Caller owns disposal via `using`.
        }
    }

    private static string Localize(ErrorCode code) => code switch
    {
        ErrorCode.InvalidRequest => "请求无效",
        ErrorCode.DependencyMissing => "PDF 依赖尚未安装",
        ErrorCode.WorkerUnavailable => "Worker 暂不可用，请重试",
        ErrorCode.TaskCancelled => "已取消",
        ErrorCode.TaskTimeout => "操作超时，请重试",
        ErrorCode.ResourceExhausted => "内存或显存不足",
        _ => "操作失败",
    };

    private void SetField<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}

public enum PdfPageState { None, Processing, Done, Failed }

public sealed class PdfPageViewModel : INotifyPropertyChanged
{
    private PdfPageState _state = PdfPageState.None;
    public event PropertyChangedEventHandler? PropertyChanged;
    public int Index { get; init; }
    public PdfPageState State
    {
        get => _state;
        set
        {
            if (_state == value) return;
            _state = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(State)));
        }
    }
}

public interface IPdfFileSource
{
    Task<string?> PickFileAsync(CancellationToken cancellationToken);
}
