using System.Buffers.Binary;

namespace VibeOCR.Platform.Worker;

public static class FrameCodec
{
    public const int DefaultMaxFrameBytes = 8 << 20;
    private const int PrefixBytes = sizeof(uint);

    public static async ValueTask WriteAsync(
        Stream stream,
        ReadOnlyMemory<byte> payload,
        CancellationToken cancellationToken = default,
        int maxFrameBytes = DefaultMaxFrameBytes)
    {
        ArgumentNullException.ThrowIfNull(stream);
        ArgumentOutOfRangeException.ThrowIfNegative(maxFrameBytes);
        if (payload.Length > maxFrameBytes)
        {
            throw new InvalidDataException(
                $"Frame length {payload.Length} exceeds cap {maxFrameBytes}.");
        }

        byte[] prefix = new byte[PrefixBytes];
        BinaryPrimitives.WriteUInt32LittleEndian(prefix, checked((uint)payload.Length));
        await stream.WriteAsync(prefix, cancellationToken).ConfigureAwait(false);
        await stream.WriteAsync(payload, cancellationToken).ConfigureAwait(false);
        await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
    }

    public static async ValueTask<byte[]> ReadAsync(
        Stream stream,
        CancellationToken cancellationToken = default,
        int maxFrameBytes = DefaultMaxFrameBytes)
    {
        ArgumentNullException.ThrowIfNull(stream);
        ArgumentOutOfRangeException.ThrowIfNegative(maxFrameBytes);
        byte[] prefix = new byte[PrefixBytes];
        await ReadExactlyAsync(stream, prefix, cancellationToken).ConfigureAwait(false);
        uint length = BinaryPrimitives.ReadUInt32LittleEndian(prefix);
        if (length > maxFrameBytes)
        {
            throw new InvalidDataException($"Frame length {length} exceeds cap {maxFrameBytes}.");
        }

        byte[] payload = new byte[length];
        await ReadExactlyAsync(stream, payload, cancellationToken).ConfigureAwait(false);
        return payload;
    }

    private static async ValueTask ReadExactlyAsync(
        Stream stream,
        Memory<byte> destination,
        CancellationToken cancellationToken)
    {
        int offset = 0;
        while (offset < destination.Length)
        {
            int read = await stream.ReadAsync(destination[offset..], cancellationToken)
                .ConfigureAwait(false);
            if (read == 0)
            {
                throw new EndOfStreamException("WorkerHost pipe closed mid-frame.");
            }

            offset += read;
        }
    }
}
