from mapf_splice.confirm import cyclic_components


def test_cyclic_components_finds_multi_node_cycles_only() -> None:
    assert cyclic_components([("R1", "R2"), ("R2", "R1")]) == (("R1", "R2"),)
    assert cyclic_components([("R1", "R2"), ("R2", "R3")]) == ()


def test_cyclic_components_is_deterministic_over_input_order() -> None:
    forward = cyclic_components([("R1", "R2"), ("R2", "R3"), ("R3", "R1")])
    reverse = cyclic_components([("R3", "R1"), ("R2", "R3"), ("R1", "R2")])
    assert forward == reverse == (("R1", "R2", "R3"),)
