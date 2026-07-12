"""VibeOCR WorkerHost: the Python side of the WinUI 3 control channel.

This package never imports PySide6. It exposes:
- contracts: versioned wire DTOs (envelope, payloads)
- errors: stable error codes shared with the C# client
- framing: length-prefixed JSON framing over an async byte stream
- named_pipe: current-user-isolated Named Pipe server (Task 1.3)
- shared_payload: Windows shared-memory payload transfer (Task 1.4)
- dispatcher / task_registry / session / handlers / main (Tasks 1.5/1.6)
"""
