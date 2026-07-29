# VibeOCR Runtime Client

Thin C# transport for the VibeOCR Runtime Protocol. It pins requests to a
loopback base URL, attaches the per-process bearer token, supports generated
operation paths, and converts protocol error envelopes into typed exceptions.
