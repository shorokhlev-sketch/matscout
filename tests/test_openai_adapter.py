"""OpenAI tool-spec generation — schema sanity + name/description propagation."""

from __future__ import annotations

from matscout.agent import tool_to_openai_spec, tools_to_openai_specs
from matscout.tool_facades import (
    check_stability,
    compare_materials,
    get_material,
    search_materials,
)


def test_get_material_spec_has_required_string_id() -> None:
    spec = tool_to_openai_spec(get_material)
    fn = spec["function"]
    assert fn["name"] == "get_material"
    assert "material_id" in fn["parameters"]["properties"]
    assert fn["parameters"]["properties"]["material_id"]["type"] == "string"
    assert fn["parameters"]["required"] == ["material_id"]


def test_check_stability_spec_minimal() -> None:
    spec = tool_to_openai_spec(check_stability)
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "check_stability"
    assert "material_id" in spec["function"]["parameters"]["properties"]


def test_compare_materials_spec_list_arg() -> None:
    spec = tool_to_openai_spec(compare_materials)
    props = spec["function"]["parameters"]["properties"]
    assert props["material_ids"]["type"] == "array"
    assert props["material_ids"]["items"]["type"] == "string"
    # properties (the kwarg) is optional → not in required
    assert "material_ids" in spec["function"]["parameters"]["required"]
    assert "properties" not in spec["function"]["parameters"].get("required", [])


def test_search_materials_spec_has_filter_fields() -> None:
    spec = tool_to_openai_spec(search_materials)
    props = spec["function"]["parameters"]["properties"]
    # All search knobs are surfaced as flat kwargs
    for key in (
        "elements",
        "band_gap_range",
        "only_stable",
        "max_energy_above_hull",
        "limit",
    ):
        assert key in props, f"missing flat kwarg: {key}"
    assert props["only_stable"]["type"] == "boolean"
    assert props["limit"]["type"] == "integer"


def test_description_is_first_doc_paragraph() -> None:
    spec = tool_to_openai_spec(get_material)
    # `get_material` docstring's first paragraph mentions "full property sheet"
    assert "property sheet" in spec["function"]["description"].lower()


def test_bulk_helper_returns_all_specs() -> None:
    specs = tools_to_openai_specs(
        [search_materials, get_material, compare_materials, check_stability]
    )
    names = [s["function"]["name"] for s in specs]
    assert names == ["search_materials", "get_material", "compare_materials", "check_stability"]


def test_specs_are_json_serializable() -> None:
    """Schemas must be plain JSON for OpenAI's HTTP API to accept them."""
    import json

    specs = tools_to_openai_specs([get_material, check_stability])
    json.dumps(specs)  # must not raise
