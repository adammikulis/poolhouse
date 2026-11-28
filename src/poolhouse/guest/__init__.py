"""Guest access: letting someone outside the pool ask a model on this computer, and asking one on theirs (`docs/guest.md`).

A guest is not a pool member. Her computer and the host each pin the other's certificate after one joining; her requests reach one
listener that answers model calls and nothing else, and are served by a private model server that keeps nothing on disk
(`poolhouse.serve.private`). Nothing in this package writes a request or a reply anywhere.
"""
