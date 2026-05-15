import re as re_module

import click

from .analysis import Artifact, ArtifactType, list_artifacts
from .project import Project


class Filter:
    KEY: str = ""
    DOC: str = ""

    def matches(self, artifact: Artifact) -> bool:
        raise NotImplementedError

    @classmethod
    def from_clause(cls, rest: str) -> "Filter":
        raise NotImplementedError


class FileFilter(Filter):
    KEY = "file"
    DOC = "file:<filename>  Only include artifacts that have a file named <filename>"

    def __init__(self, filename: str):
        self.filename = filename

    @classmethod
    def from_clause(cls, rest: str) -> "FileFilter":
        if not rest:
            raise ValueError("file: requires a filename")
        return cls(rest)

    def matches(self, artifact: Artifact) -> bool:
        return (artifact.dir / self.filename).exists()


class MetaFilter(Filter):
    KEY = "meta"
    DOC = "meta:<key>=<value>  Only include artifacts where meta field <key> equals <value>"

    def __init__(self, key: str, value: str):
        self.key = key
        self.value = value

    @classmethod
    def from_clause(cls, rest: str) -> "MetaFilter":
        if "=" not in rest:
            raise ValueError(f"meta: clause must be key=value, got: {rest!r}")
        k, v = rest.split("=", 1)
        return cls(k, v)

    def matches(self, artifact: Artifact) -> bool:
        return str(artifact.meta.get(self.key)) == self.value


class ReFilter(Filter):
    KEY = "re"
    DOC = "re:<filename>:<pattern>  Only include artifacts where <filename> contains a match for regex <pattern>"

    def __init__(self, filename: str, pattern: re_module.Pattern):
        self.filename = filename
        self.pattern = pattern

    @classmethod
    def from_clause(cls, rest: str) -> "ReFilter":
        if ":" not in rest:
            raise ValueError(f"re: clause must be <filename>:<pattern>, got: {rest!r}")
        filename, pattern_str = rest.split(":", 1)
        try:
            compiled = re_module.compile(pattern_str)
        except re_module.error as e:
            raise ValueError(f"re: invalid regex {pattern_str!r}: {e}")
        return cls(filename, compiled)

    def matches(self, artifact: Artifact) -> bool:
        p = artifact.dir / self.filename
        return p.exists() and bool(self.pattern.search(p.read_text(errors="replace")))


class TypeFilter(Filter):
    KEY = "type"
    DOC = "type:<crash|core>  Only include artifacts of the given type"

    def __init__(self, atype: ArtifactType):
        self.atype = atype

    @classmethod
    def from_clause(cls, rest: str) -> "TypeFilter":
        try:
            return cls(ArtifactType(rest))
        except ValueError:
            valid = ", ".join(t.value for t in ArtifactType)
            raise ValueError(f"type: must be one of {valid}, got: {rest!r}")

    def matches(self, artifact: Artifact) -> bool:
        return artifact.type == self.atype


class NoFilter(Filter):
    KEY = "no"
    DOC = "no:<clause>  Invert <clause>, excluding any artifact that would match it"

    def __init__(self, inner: Filter):
        self.inner = inner

    @classmethod
    def from_clause(cls, rest: str) -> "NoFilter":
        return cls(make_filter(rest))

    def matches(self, artifact: Artifact) -> bool:
        return not self.inner.matches(artifact)


_FILTERS: list[type[Filter]] = [FileFilter, MetaFilter, ReFilter, TypeFilter, NoFilter]


def make_filter(clause: str) -> Filter:
    key, _, rest = clause.partition(":")
    for cls in _FILTERS:
        if cls.KEY == key:
            return cls.from_clause(rest)
    valid = ", ".join(f"{c.KEY}:" for c in _FILTERS)
    raise ValueError(f"Unknown filter clause {clause!r}. Valid prefixes: {valid}")


async def query_artifacts(project: Project, clauses: list[str]) -> list[Artifact]:
    filters = [make_filter(c) for c in clauses]
    artifacts = await list_artifacts(project)
    return sorted(
        (a for a in artifacts if all(f.matches(a) for f in filters)),
        key=lambda a: a.hash,
    )


class QueryCommand(click.Command):
    def format_help(self, ctx, formatter):
        super().format_help(ctx, formatter)
        with formatter.section("Filter clauses"):
            formatter.write_dl([tuple(cls.DOC.split("  ", 1)) for cls in _FILTERS])
