import pytest
from cli.isa import build_parser


def test_run_accepts_mode_and_stage():
    a = build_parser().parse_args(["run", "--mode", "first-time", "--stage", "enrich"])
    assert a.command == "run" and a.mode == "first-time" and a.stage == "enrich"


def test_run_mode_defaults_to_incremental():
    a = build_parser().parse_args(["run"])
    assert a.mode == "incremental" and a.stage is None


def test_invalid_mode_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--mode", "bogus"])


def test_invalid_stage_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--stage", "teleport"])


def test_subcommands_exist():
    for cmd in (["discover"], ["status"], ["backup", "--restore-check"]):
        assert build_parser().parse_args(cmd).command == cmd[0]
