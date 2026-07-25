using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace VibeOCR.App.Features.Batch;

public enum BatchItemState { Pending, Running, Completed, Failed, Cancelled }

public sealed class BatchItemViewModel(string path) : INotifyPropertyChanged
{
    private BatchItemState _state = BatchItemState.Pending;
    private RecognizeResponse? _result;
    private string? _error;

    public event PropertyChangedEventHandler? PropertyChanged;
    public Guid Id { get; } = Guid.NewGuid();
    public string Path { get; } = System.IO.Path.GetFullPath(path);
    public string Name => System.IO.Path.GetFileName(Path);
    public BatchItemState State { get => _state; internal set => Set(ref _state, value); }
    public RecognizeResponse? Result { get => _result; internal set => Set(ref _result, value); }
    public string? Error { get => _error; internal set => Set(ref _error, value); }

    internal void Reset()
    {
        Result = null;
        Error = null;
        State = BatchItemState.Pending;
    }

    private void Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
