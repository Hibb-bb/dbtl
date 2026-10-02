class HarnessError(Exception):
    """A rule violation. `rule` is a stable machine name; `message` is for humans."""

    def __init__(self, rule: str, message: str):
        self.rule = rule
        self.message = message
        super().__init__(f"[{rule}] {message}")


class StateError(HarnessError):
    """Called out of order. `next_step` names the legal next script."""

    def __init__(self, message: str, next_step: str):
        self.next_step = next_step
        super().__init__("state", f"{message} Next legal step: {next_step}.")
