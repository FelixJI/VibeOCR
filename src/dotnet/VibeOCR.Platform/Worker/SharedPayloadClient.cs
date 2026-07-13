using System.Collections.Concurrent;
using System.IO.MemoryMappedFiles;
using System.Security.Cryptography;
using VibeOCR.Contracts;

namespace VibeOCR.Platform.Worker;

public sealed class SharedPayloadClient : IAsyncDisposable
{
    private readonly ConcurrentDictionary<string, MemoryMappedFile> _owned = new();
    private readonly Guid _sessionId;

    public SharedPayloadClient(Guid sessionId)
    {
        byte[] bytes = sessionId.ToByteArray();
        if (sessionId == Guid.Empty || (bytes[7] >> 4) != 4 || (bytes[8] & 0xc0) != 0x80)
        {
            throw new ArgumentException("Session id must be an RFC 4122 version 4 UUID.", nameof(sessionId));
        }

        _sessionId = sessionId;
    }

    public SharedPayloadRef Create(ReadOnlySpan<byte> data, string mediaType, TimeSpan ttl)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(mediaType);
        if (ttl < TimeSpan.Zero)
        {
            throw new ArgumentOutOfRangeException(nameof(ttl));
        }

        string name = $@"Local\VibeOCR-{_sessionId:D}-{Guid.NewGuid():D}";
        var mapping = MemoryMappedFile.CreateNew(name, Math.Max(1, data.Length));
        using (MemoryMappedViewAccessor view = mapping.CreateViewAccessor(0, data.Length, MemoryMappedFileAccess.Write))
        {
            view.WriteArray(0, data.ToArray(), 0, data.Length);
        }

        if (!_owned.TryAdd(name, mapping))
        {
            mapping.Dispose();
            throw new InvalidOperationException("Duplicate shared payload name.");
        }

        var reference = new SharedPayloadRef
        {
            Name = name,
            Size = data.Length,
            MediaType = mediaType,
            Sha256 = Convert.ToHexStringLower(SHA256.HashData(data)),
            Owner = SharedPayloadOwner.Client,
            ExpiresUnixMs = DateTimeOffset.UtcNow.Add(ttl).ToUnixTimeMilliseconds(),
        };
        try
        {
            reference.Validate();
            return reference;
        }
        catch
        {
            Release(name);
            throw;
        }
    }

    public byte[] Read(SharedPayloadRef reference)
    {
        reference.Validate();
        if (reference.Size > int.MaxValue)
        {
            throw new InvalidDataException("Shared payload is too large for a managed buffer.");
        }
        if (reference.ExpiresUnixMs < DateTimeOffset.UtcNow.ToUnixTimeMilliseconds())
        {
            throw new InvalidDataException("Shared payload has expired.");
        }

        using MemoryMappedFile mapping = MemoryMappedFile.OpenExisting(
            reference.Name,
            MemoryMappedFileRights.Read);
        using MemoryMappedViewAccessor view = mapping.CreateViewAccessor(
            0,
            reference.Size,
            MemoryMappedFileAccess.Read);
        byte[] data = new byte[checked((int)reference.Size)];
        view.ReadArray(0, data, 0, data.Length);
        string actual = Convert.ToHexStringLower(SHA256.HashData(data));
        if (!CryptographicOperations.FixedTimeEquals(
                Convert.FromHexString(actual),
                Convert.FromHexString(reference.Sha256)))
        {
            throw new InvalidDataException("Shared payload SHA-256 mismatch.");
        }

        return data;
    }

    public bool Release(string name)
    {
        if (_owned.TryRemove(name, out MemoryMappedFile? mapping))
        {
            mapping.Dispose();
            return true;
        }

        return false;
    }

    public ValueTask DisposeAsync()
    {
        foreach ((string name, MemoryMappedFile mapping) in _owned)
        {
            if (_owned.TryRemove(name, out _))
            {
                mapping.Dispose();
            }
        }

        return ValueTask.CompletedTask;
    }
}
