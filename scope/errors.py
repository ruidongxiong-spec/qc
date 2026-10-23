"""Errors crossing model and runner boundaries."""


class ModelOutputError(ValueError):
    """The model replied, but its response failed the output contract."""


class ModelTransportError(RuntimeError):
    """The model service could not complete a request; do not retry silently."""
