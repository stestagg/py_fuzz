import asyncio
from pathlib import Path
import shutil
import click
import os

from .paths import gh_path
from .pr import load_pr, pr_add_value

@click.group()
def cli():
    pass

@cli.group()
def pr():
    pass

@pr.command()
def sync():
    from .pr import sync_prs
    asyncio.run(sync_prs())

@pr.command()
@click.option('-q', '--quiet', is_flag=True, help="Only print PR numbers")
@click.option('-n', '--number', type=int, help="Only return the top N results", default=20)
@click.option('--no', type=str, help='Return rows that do not have any value in this column')
@click.option('-s', '--sort', type=str, help='Sort by values in the given column')
@click.option('--asc', is_flag=True, help='Sort in ascending order (default is descending)')
def list(quiet, number, no, sort, asc):
    from .pr import load_prs
    df = load_prs()
    if sort:
        df = df.sort_values(sort, ascending=asc)
    if no:
        if no not in df.columns:
            print(f"WARNING: Column '{no}' not found in PR data, no filtering applied.")
        else:
            isna_rows = df[no].isna()
            blank_rows = df[no] == ""
            df = df[(isna_rows | blank_rows)]

    df = df.head(number)
    if quiet:
        print("\n".join(str(n) for n in df["number"]))
    else:
        print(df.to_markdown(index=False, maxcolwidths=[20] * len(df.columns), tablefmt="rounded_grid"))


@pr.command()
@click.argument("pr_numbers", type=int, nargs=-1)
def preclassify(pr_numbers):
    from .preclassify import preclassify_prs
    asyncio.run(preclassify_prs(pr_numbers))


@pr.command()
@click.argument("pr_number", type=int)
def describe(pr_number):
    pr_data = load_pr(pr_number)
    from .prdesc import describe_pr
    description = asyncio.run(describe_pr(pr_data))
    print(description)

@pr.command()
@click.argument("pr_numbers", type=int, nargs=-1)
def gen_tests(pr_numbers):
    from .testcases import generate_test_cases
    asyncio.run(generate_test_cases(pr_numbers))


# ./pfgh make-project fuzz-proj-1 (./pfgh pr list -q -n 5 --no project -s risk_score) 
@cli.command()
@click.argument("project_name", type=str)
@click.argument("pr_numbers", type=int, nargs=-1)
def make_project(project_name, pr_numbers):
    from .testcases import generate_test_cases
    asyncio.run(generate_test_cases(pr_numbers))

    print("Creating project...")
    from ..project import Project
    proj = Project.create(project_name)

    proj_inputs_dir = proj.path('inputs')
    print("Adding inputs to project...")
    for pr_number in pr_numbers:
        dest = proj_inputs_dir / f"{pr_number}"
        dest.mkdir(exist_ok=True)
        tests_path = gh_path('inputs') / f"{pr_number}"
        if tests_path.exists():
            shutil.copytree(tests_path, dest, dirs_exist_ok=True)
    print(f"Project created at {proj.path()}") 

    for pr_number in pr_numbers:
        pr_add_value(pr_number, "project", project_name)