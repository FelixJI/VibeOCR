using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace VibeOCR.Contracts;

[JsonConverter(typeof(JsonStringEnumConverter<SharedPayloadOwner>))]
public enum SharedPayloadOwner
{
    [JsonStringEnumMemberName("client")]
    Client,
    [JsonStringEnumMemberName("worker")]
    Worker,
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record SharedPayloadRef : IProtocolValidatable
{
    private static readonly Regex NamePattern = new(
        @"^Local\\VibeOCR-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        RegexOptions.CultureInvariant | RegexOptions.NonBacktracking);
    private static readonly Regex Sha256Pattern = new(
        "^[0-9a-f]{64}$",
        RegexOptions.CultureInvariant | RegexOptions.NonBacktracking);

    public required string Name { get; init; }
    public required long Size { get; init; }
    public required string MediaType { get; init; }
    public required string Sha256 { get; init; }
    public required SharedPayloadOwner Owner { get; init; }
    public required long ExpiresUnixMs { get; init; }

    public void Validate()
    {
        ValidateName(Name);
        if (!Sha256Pattern.IsMatch(Sha256))
        {
            throw new ProtocolContractException("Shared payload SHA-256 is invalid.");
        }

        ContractValidation.NonEmpty(MediaType, nameof(MediaType));
        if (Size < 0 || ExpiresUnixMs < 0)
        {
            throw new ProtocolContractException("Shared payload size/expiry must be non-negative.");
        }

        if (!Enum.IsDefined(Owner))
        {
            throw new ProtocolContractException("Shared payload owner is invalid.");
        }
    }

    internal static void ValidateName(string name)
    {
        if (!NamePattern.IsMatch(name))
        {
            throw new ProtocolContractException("Shared payload name is invalid.");
        }
    }
}
