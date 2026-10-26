"""One deadline for a multi-request read, independent of per-request timeouts."""
from contextlib import contextmanager
import time


class BoundedQueries:
    operation_deadline = None

    @contextmanager
    def bounded(self, seconds):
        previous = self.operation_deadline
        end = time.monotonic() + max(0, seconds)
        self.operation_deadline = min(previous, end) if previous is not None else end
        try:
            yield self
        finally:
            self.operation_deadline = previous

    def deadline(self):
        end = time.monotonic() + self.timeout
        if self.operation_deadline is not None:
            end = min(end, self.operation_deadline)
        if end <= time.monotonic():
            raise TimeoutError('本次查询超过总时限，请稍后刷新')
        return end
