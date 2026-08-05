import pytest

from figmint.cli import build_parser


class TestParser:
    def test_it_reports_a_version_and_still_wants_a_command(self, capsys):
        parser = build_parser()
        # `--version` is resolved when asked for rather than when the parser is
        # built, so what matters is that it still prints something version-like
        # and exits cleanly.
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out.startswith("figmint ")
        assert out.removeprefix("figmint ")[0].isdigit()
        # Nothing else changed about the parser: a bare invocation is an error.
        with pytest.raises(SystemExit) as exc:
            parser.parse_args([])
        assert exc.value.code != 0
