import argparse

from collectors.__main__ import build_parser


def test_collector_parser_constructs_with_unique_commands():
    parser = build_parser()
    action = next(
        value for value in parser._actions
        if isinstance(value, argparse._SubParsersAction))
    names = list(action.choices)
    assert len(names) == len(set(names))
    assert "connectors" in names


def test_connector_registry_commands_still_parse():
    listed = build_parser().parse_args(["connectors", "list"])
    assert (listed.command, listed.action, listed.connector) == (
        "connectors", "list", None)
    inspected = build_parser().parse_args([
        "connectors", "inspect", "mist", "--json"])
    assert (inspected.command, inspected.action, inspected.connector) == (
        "connectors", "inspect", "mist")
    assert inspected.json is True


def test_entrypoint_parser_construction_has_no_side_effects():
    # Regression for argparse.ArgumentError: conflicting subparser: connectors.
    assert build_parser().prog
