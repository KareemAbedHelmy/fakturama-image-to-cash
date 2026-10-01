"""Error messages contain codes, never untrusted source text or personal data."""


class ReviewRequired(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
