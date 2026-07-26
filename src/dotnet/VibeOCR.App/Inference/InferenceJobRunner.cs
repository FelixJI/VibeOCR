using System.Text.Json;
using VibeOCR.Contracts.HttpV2;
using VibeOCR.Platform.Inference;

namespace VibeOCR.App.Inference;

/// <summary>
/// One upload in a logical recognition job. <see cref="ClientItemKey"/> is
/// stable UI identity; transport attachment names and server item ids stay
/// hidden inside <see cref="InferenceJobRunner"/>.
/// </summary>
internal sealed record InferenceUploadInput(
    string ClientItemKey,
    string DisplayName,
    string? ContentType,
    IReadOnlyList<byte> Content);

/// <summary>A terminal job snapshot plus outcomes keyed by UI-owned identity.</summary>
internal sealed record InferenceJobRun(
    JobSnapshot Snapshot,
    IReadOnlyDictionary<string, ItemOutcome> OutcomesByClientItemKey);

/// <summary>
/// Deep App-side module for the generic inference job lifecycle.
///
/// It creates one logical recognition manifest, atomically observes snapshot,
/// events and outcome deltas, aligns server item ids back to client item keys,
/// and propagates cancellation through the generic command seam.
/// </summary>
internal sealed class InferenceJobRunner(IInferenceClient inference)
{
    public async Task<InferenceJobRun> RunRecognitionAsync(
        string pipelineId,
        JobPriority priority,
        IReadOnlyList<InferenceUploadInput> inputs,
        IReadOnlyDictionary<string, JsonElement>? options,
        CancellationToken cancellationToken)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(pipelineId);
        ArgumentNullException.ThrowIfNull(inputs);
        if (inputs.Count == 0)
        {
            throw new ArgumentException("A logical job must contain at least one input.", nameof(inputs));
        }

        EnsureUniqueClientKeys(inputs);
        var submitItems = new SubmitItem[inputs.Count];
        var uploads = new Dictionary<string, SubmitUpload>(inputs.Count, StringComparer.Ordinal);
        for (int ordinal = 0; ordinal < inputs.Count; ordinal++)
        {
            InferenceUploadInput input = inputs[ordinal];
            string attachment = $"input-{ordinal}";
            submitItems[ordinal] = new SubmitItem
            {
                ClientItemKey = input.ClientItemKey,
                Ordinal = ordinal,
                DisplayName = input.DisplayName,
                Source = new Dictionary<string, JsonElement>
                {
                    ["type"] = JsonSerializer.SerializeToElement("upload.v1"),
                    ["attachment"] = JsonSerializer.SerializeToElement(attachment),
                },
            };
            uploads.Add(attachment, new SubmitUpload(input.ContentType, input.Content));
        }

        var request = new SubmitRequest
        {
            RequestId = Guid.NewGuid().ToString("N"),
            Kind = JobKind.Recognition,
            Priority = priority,
            Pipeline = new PipelineSelection
            {
                PipelineId = pipelineId,
                Options = options is null
                    ? new Dictionary<string, JsonElement>()
                    : new Dictionary<string, JsonElement>(options, StringComparer.Ordinal),
            },
            Items = submitItems,
        };

        JobRef? job = null;
        try
        {
            job = await inference.SubmitAsync(request, uploads, cancellationToken);
            IReadOnlyDictionary<string, string> clientKeyByItemId =
                ValidateAndIndexJobRef(job, inputs);
            var outcomes = new Dictionary<string, ItemOutcome>(StringComparer.Ordinal);
            int afterSequence = 0;

            while (true)
            {
                cancellationToken.ThrowIfCancellationRequested();
                JobUpdate update = await inference.ObserveAsync(
                    job.JobId,
                    afterSequence,
                    cancellationToken);
                ValidateUpdate(job.JobId, afterSequence, update);
                AccumulateOutcomes(clientKeyByItemId, outcomes, update.Outcomes);

                bool terminal = IsTerminal(update.Snapshot.State);
                if (terminal && !update.More)
                {
                    if (outcomes.Count != inputs.Count)
                    {
                        throw ProtocolViolation(
                            $"Terminal job '{job.JobId}' returned {outcomes.Count} outcomes "
                            + $"for {inputs.Count} submitted items.");
                    }

                    return new InferenceJobRun(update.Snapshot, outcomes);
                }

                if (update.More && update.ThroughSequence <= afterSequence)
                {
                    throw ProtocolViolation(
                        $"Job '{job.JobId}' reported more data without advancing its sequence.");
                }

                afterSequence = update.ThroughSequence;
            }
        }
        catch (OperationCanceledException)
        {
            if (job is not null)
            {
                await TryCancelAsync(job.JobId);
            }

            throw;
        }
    }

    private async Task TryCancelAsync(string jobId)
    {
        try
        {
            await inference.CommandAsync(
                new JobCommand
                {
                    CommandId = Guid.NewGuid().ToString("N"),
                    Kind = JobCommandKind.Cancel,
                    JobId = jobId,
                },
                CancellationToken.None);
        }
        catch (Exception)
        {
            // Best effort: cancellation of the local wait must not be masked
            // by a supervisor that is already shutting down or disconnected.
        }
    }

    private static void EnsureUniqueClientKeys(IReadOnlyList<InferenceUploadInput> inputs)
    {
        var keys = new HashSet<string>(StringComparer.Ordinal);
        foreach (InferenceUploadInput input in inputs)
        {
            if (string.IsNullOrWhiteSpace(input.ClientItemKey) || !keys.Add(input.ClientItemKey))
            {
                throw new ArgumentException(
                    "Client item keys must be non-empty and unique.",
                    nameof(inputs));
            }
        }
    }

    private static IReadOnlyDictionary<string, string> ValidateAndIndexJobRef(
        JobRef job,
        IReadOnlyList<InferenceUploadInput> inputs)
    {
        if (job.Items.Count != inputs.Count)
        {
            throw ProtocolViolation(
                $"JobRef returned {job.Items.Count} item mappings for {inputs.Count} inputs.");
        }

        var expectedKeys = inputs
            .Select(input => input.ClientItemKey)
            .ToHashSet(StringComparer.Ordinal);
        var clientKeyByItemId = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (JobItem item in job.Items)
        {
            if (string.IsNullOrWhiteSpace(item.ItemId)
                || string.IsNullOrWhiteSpace(item.ClientItemKey)
                || !expectedKeys.Remove(item.ClientItemKey)
                || !clientKeyByItemId.TryAdd(item.ItemId, item.ClientItemKey))
            {
                throw ProtocolViolation("JobRef contains an invalid or duplicate item mapping.");
            }
        }

        if (expectedKeys.Count != 0)
        {
            throw ProtocolViolation("JobRef omitted one or more submitted client item keys.");
        }

        return clientKeyByItemId;
    }

    private static void ValidateUpdate(string jobId, int afterSequence, JobUpdate update)
    {
        if (!string.Equals(update.Snapshot.JobId, jobId, StringComparison.Ordinal))
        {
            throw ProtocolViolation("Observed snapshot belongs to a different job.");
        }

        if (update.ThroughSequence < afterSequence)
        {
            throw ProtocolViolation("Observed sequence moved backwards.");
        }
    }

    private static void AccumulateOutcomes(
        IReadOnlyDictionary<string, string> clientKeyByItemId,
        IDictionary<string, ItemOutcome> outcomes,
        IReadOnlyList<ItemOutcome> additions)
    {
        foreach (ItemOutcome outcome in additions)
        {
            if (!clientKeyByItemId.TryGetValue(outcome.ItemId, out string? clientKey))
            {
                throw ProtocolViolation(
                    $"Outcome references unknown server item id '{outcome.ItemId}'.");
            }

            if (!outcomes.TryAdd(clientKey, outcome))
            {
                throw ProtocolViolation(
                    $"Job returned more than one terminal outcome for client item '{clientKey}'.");
            }
        }
    }

    private static bool IsTerminal(JobState state) => state is
        JobState.Completed
        or JobState.CompletedWithErrors
        or JobState.Cancelled
        or JobState.Failed;

    private static InferenceClientException ProtocolViolation(string message) =>
        new(HttpV2ErrorCode.ProtocolMismatch, message, retryable: false);
}

internal static class RecognitionOutcomeMapper
{
    public static RecognizeResponse ToResponse(ItemOutcome outcome, string pipeline)
    {
        if (outcome.State is not ItemState.Succeeded || outcome.Payload is null)
        {
            throw new InvalidOperationException("Only successful outcomes contain recognition results.");
        }

        IDictionary<string, JsonElement> payload = outcome.Payload;
        string rawText = StringValue(payload, "raw_text")
            ?? StringValue(payload, "text")
            ?? string.Empty;
        return new RecognizeResponse
        {
            Text = rawText,
            RawText = rawText,
            MarkdownText = StringValue(payload, "markdown_text"),
            HtmlText = StringValue(payload, "html_text"),
            RawBlocks = ArrayValue(payload, "text_blocks"),
            Pipeline = pipeline,
        };
    }

    private static string? StringValue(
        IDictionary<string, JsonElement> payload,
        string key) =>
        payload.TryGetValue(key, out JsonElement value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static JsonElement[]? ArrayValue(
        IDictionary<string, JsonElement> payload,
        string key) =>
        payload.TryGetValue(key, out JsonElement value)
        && value.ValueKind == JsonValueKind.Array
            ? value.EnumerateArray().Select(element => element.Clone()).ToArray()
            : null;
}
