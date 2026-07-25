from schemas import ConfigRequest, TypeDef


def test_typedef_flags_default_false() -> None:
    t = TypeDef(name="Person", description="d")
    assert t.pinned is False and t.banned is False


def test_config_request_has_interests_and_discover() -> None:
    req = ConfigRequest(
        interests="what I care about",
        discover_types=True,
        entity_types=[TypeDef(name="Person", description="", pinned=True)],
        relationship_types=[],
    )
    assert req.interests == "what I care about"
    assert req.entity_types[0].pinned is True
