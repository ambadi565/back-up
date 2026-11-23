from drive_backup.settle import Settler


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_ready_after_debounce():
    c = Clock()
    s = Settler(debounce_s=5, max_wait_s=1800, clock=c)
    s.touch("k")
    c.t = 4
    assert s.ready() == []
    assert s.pending_count() == 1
    c.t = 5
    assert s.ready() == [("k", False)]
    assert s.pending_count() == 0


def test_forced_after_max_wait():
    c = Clock()
    s = Settler(debounce_s=5, max_wait_s=1800, clock=c)
    t = 0
    while t < 1800:
        c.t = t
        s.touch("k")
        assert s.ready() == []
        t += 3
    c.t = 1800
    assert s.ready() == [("k", True)]
