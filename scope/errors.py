"""Errors crossing model and runner boundaries."""


class ModelOutputError(ValueError):
    """The model replied, but its response failed the output contract."""

    def __init__(self, message, response_preview=None):
        super().__init__(message)
        self.response_preview = response_preview


class ModelGenerationError(ModelOutputError):
    """A recognized generation abort; recovery consumes existing output/step budgets."""


class ModelTransportError(RuntimeError):
    """The model service could not complete a request; do not retry silently."""
