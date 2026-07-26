// Phase 7B tests: SettingsViewModel v2 residency path.
//
// Verifies plan §7B: Settings reads residency status (TTL/pin/LRU/VRAM) via
// the v2 supervisor client (IInferenceClient.GetResidencyAsync). The legacy
// settings.snapshot path stays covered by SettingsViewModelTests; this file
// is additive.
using VibeOCR.App.Features.Settings;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;
using Xunit;

namespace VibeOCR.App.Tests;

public sealed class SettingsViewModelSupervisorTests
{
    [Fact]
    public async Task LoadResidencyPopulatesV2Observables()
    {
        var fake = new FakeSettingsInferenceClient(defaultTtl: 600, vramTotal: 24576, vramUsed: 2000);
        fake.Entries.Add(new ResidencyEntry
        {
            Pipeline = "OCR",
            Kind = ResidencyKind.SoftTtl,
            ActiveLeases = 1,
            RemainingTtlSeconds = 240,
            EstimatedVramMb = 1200,
        });
        fake.Entries.Add(new ResidencyEntry
        {
            Pipeline = "MinerU",
            Kind = ResidencyKind.Pinned,
            ActiveLeases = 0,
            EstimatedVramMb = 800,
        });
        fake.Pipelines.Add(new PipelineSpec { Name = "OCR", TtlSeconds = null, Pinned = false });
        fake.Pipelines.Add(new PipelineSpec { Name = "MinerU", TtlSeconds = 600, Pinned = false });

        var viewModel = new SettingsViewModel(fake);

        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);

        Assert.Equal(600, viewModel.DefaultTtlSeconds);
        Assert.Equal(24576, viewModel.VramTotalMb);
        Assert.Equal(2000, viewModel.VramUsedMb);
        Assert.Equal(2, viewModel.ResidencyEntries.Count);
        Assert.Equal("OCR", viewModel.ResidencyEntries[0].Pipeline);
        Assert.Equal(ResidencyKind.SoftTtl, viewModel.ResidencyEntries[0].Kind);
        Assert.Equal("MinerU", viewModel.ResidencyEntries[1].Pipeline);
        Assert.Equal(ResidencyKind.Pinned, viewModel.ResidencyEntries[1].Kind);
        Assert.Equal(2, viewModel.ResidencyPipelines.Count);
        Assert.False(viewModel.IsBusy);
        Assert.Contains("600", viewModel.Status);
    }

    [Fact]
    public async Task LoadResidencyLocalizesTypedError()
    {
        var fake = new FakeSettingsInferenceClient(
            residencyThrows: new InferenceClientException(HttpV2ErrorCode.OutOfMemory, "oom", true));
        var viewModel = new SettingsViewModel(fake);

        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);

        Assert.Equal("内存或显存不足", viewModel.Status);
        Assert.False(viewModel.IsBusy);
    }

    [Fact]
    public async Task LoadResidencyReportsSupervisorDisconnect()
    {
        var fake = new FakeSettingsInferenceClient(
            residencyThrows: new InferenceClientException(HttpV2ErrorCode.BackendUnavailable, "down", true));
        var viewModel = new SettingsViewModel(fake);

        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);

        Assert.Equal("Supervisor 暂不可用，请重试", viewModel.Status);
    }

    [Fact]
    public async Task LoadSnapshotCompletesSuccessfully()
    {
        var viewModel = new SettingsViewModel(new FakeSettingsInferenceClient(defaultTtl: 300));
        await viewModel.LoadSnapshotAsync(CancellationToken.None);
        Assert.Equal(300, viewModel.DefaultTtlSeconds);
        Assert.False(viewModel.IsBusy);
    }

    [Fact]
    public async Task LoadResidencyEmptyStatusIsHarmless()
    {
        // A supervisor with no loaded models: empty entries/pipelines, no VRAM.
        var fake = new FakeSettingsInferenceClient(defaultTtl: 300, vramTotal: null, vramUsed: null);
        var viewModel = new SettingsViewModel(fake);

        await viewModel.LoadSnapshotAsync(TestContext.Current.CancellationToken);

        Assert.Equal(300, viewModel.DefaultTtlSeconds);
        Assert.Null(viewModel.VramTotalMb);
        Assert.Null(viewModel.VramUsedMb);
        Assert.Empty(viewModel.ResidencyEntries);
        Assert.Empty(viewModel.ResidencyPipelines);
        Assert.Contains("0", viewModel.Status); // "已驻留管线 0 个"
    }

    // ------------------------------------------------------------------
    // Fakes
    // ------------------------------------------------------------------

    private sealed class FakeSettingsInferenceClient : InferenceClientStub
    {
        private readonly int _defaultTtl;
        private readonly int? _vramTotal;
        private readonly int? _vramUsed;
        private readonly InferenceClientException? _residencyThrows;

        public FakeSettingsInferenceClient(
            int defaultTtl = 300,
            int? vramTotal = null,
            int? vramUsed = null,
            InferenceClientException? residencyThrows = null)
        {
            _defaultTtl = defaultTtl;
            _vramTotal = vramTotal;
            _vramUsed = vramUsed;
            _residencyThrows = residencyThrows;
        }

        public List<ResidencyEntry> Entries { get; } = [];
        public List<PipelineSpec> Pipelines { get; } = [];
        public override Task<ResidencyStatus> GetResidencyAsync(CancellationToken cancellationToken)
        {
            if (_residencyThrows is not null)
            {
                throw _residencyThrows;
            }

            return Task.FromResult(new ResidencyStatus
            {
                DefaultTtlSeconds = _defaultTtl,
                Entries = Entries,
                Pipelines = Pipelines,
                VramTotalMb = _vramTotal,
                VramUsedMb = _vramUsed,
            });
        }

        public override Task<SettingsSnapshot> GetSettingsAsync(CancellationToken cancellationToken)
            => Task.FromResult(new SettingsSnapshot());
    }

}
