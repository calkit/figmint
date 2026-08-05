"""Figmint: artifact provenance tracking."""


def __getattr__(name: str) -> str:
    """Resolve `__version__` on first use.

    Taken from the installed distribution's metadata, which hatch-vcs fills in
    from the git tag at build time; a literal here would be a second place to
    bump and the two would drift. Resolved lazily because importing
    `importlib.metadata` costs about 35 ms — most of the CLI's startup budget —
    and almost nothing asks what version this is.
    """
    if name == "__version__":
        from importlib.metadata import version

        return version("figmint-fresh")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
