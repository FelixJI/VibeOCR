"""WorkerHost RPC handlers: bridge the wire protocol to UI-free application facades.

Each handler translates an RPC payload into a facade request, invokes the facade
(offloaded to the executor since facades are synchronous), and returns a result
payload. Handlers never import PySide6.
"""
