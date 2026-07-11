"""Upstream blocked-closure over waiting -> blocking dependency edges."""
from mapf_splice.confirm import affected_scope


def test_direct_blocked_closure() -> None:
    # A: R3 waits into the R1<->R2 cycle.
    edges = [("R1", "R2"), ("R2", "R1"), ("R3", "R1")]
    assert affected_scope(edges, ("R1", "R2")) == ("R1", "R2", "R3")


def test_transitive_blocked_closure() -> None:
    # B: R4 -> R3 -> R1 into the cycle.
    edges = [("R4", "R3"), ("R3", "R1"), ("R1", "R2"), ("R2", "R1")]
    assert affected_scope(edges, ("R1", "R2")) == ("R1", "R2", "R3", "R4")


def test_unrelated_dependencies_excluded() -> None:
    # C: R5 -> R6 is a separate chain that never reaches the core.
    edges = [
        ("R4", "R3"),
        ("R3", "R1"),
        ("R1", "R2"),
        ("R2", "R1"),
        ("R5", "R6"),
    ]
    scope = affected_scope(edges, ("R1", "R2"))
    assert scope == ("R1", "R2", "R3", "R4")
    assert "R5" not in scope and "R6" not in scope


def test_multiple_blockers_included_regardless_of_edge_order() -> None:
    # D: R7 has two blockers; only one path reaches the core. Order independent.
    forward = [
        ("R1", "R2"),
        ("R2", "R1"),
        ("R7", "R9"),  # R9 is unrelated
        ("R7", "R1"),  # R7 also waits into the core
    ]
    reverse = list(reversed(forward))
    assert affected_scope(forward, ("R1", "R2")) == ("R1", "R2", "R7")
    assert affected_scope(reverse, ("R1", "R2")) == ("R1", "R2", "R7")


def test_core_only_when_no_upstream_waiters() -> None:
    edges = [("R1", "R2"), ("R2", "R1")]
    assert affected_scope(edges, ("R1", "R2")) == ("R1", "R2")
