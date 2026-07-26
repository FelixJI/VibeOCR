// Wire envelopes returned by the events/result/cancel endpoints. Public and
// in Contracts so the HttpV2JsonContext can register them for source gen.
using System.Text.Json.Serialization;

namespace VibeOCR.Contracts.HttpV2;

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record EventsEnvelope(IReadOnlyList<StageEvent> Events);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ResultsEnvelope(IReadOnlyList<ResultEntry> Results);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record CancelAck(CancelMode CancelMode);
