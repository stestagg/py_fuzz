from .pr import load_pr, pr_add_value
from .prdesc import describe_pr
from .preclassify import client
from .paths import gh_path
from tqdm.auto import tqdm

TESTS_ROOT = gh_path('inputs')
TESTS_ROOT.mkdir(exist_ok=True)

async def generate_test_cases_one(pr_number: int) -> list[str]:
    test_dir = TESTS_ROOT / f"{pr_number}"

    if test_dir.exists():
        n_tests = len(list(test_dir.iterdir()))
        if n_tests > 1:
            print(f"Test cases already exist for PR #{pr_number} at {test_dir} - skipping generation")
            return

    pr_data = load_pr(pr_number)
    pr_description = await describe_pr(pr_data)
    prompt = f"""
Consider the following cpython Pull request carefully, and generate a list of around 5 fuzz inputs that will help
guide a fuzzer to explore edge-cases, or potentially trigger crashes or other aborts in the interpreter.
The inputs should cover as wide a variety of usage patterns as possible, and should be minimal, acting as seeds
for the fuzzer rather than self-contained test cases.

It's ok to diverge from the exact implication of the pr to cover more related areas.
The PR may introduce a problem, or may indicate a general area of interest that should be explored by the fuzzer.

CRITICAL: fuzzing relies on a crash being an actionable outcome.  This means it's important to avoid inputs that:
 - use ctypes
 - raise a fatal signal, (os.kill, signal.raise_signal, etc)
 - call pickle/cpickle with input-defined data
 - adjust rlimits or other resource/process settings, unless the risk of causing a false-positive or poisioning the process is tiny. (remmeber the fuzzer will be trying to alter inputs to break things)

At the moment, there is a lot of 'free threaded' change happening.  These changes relate to a new python
execution mode that is NOT BEING FUZZED, do not try to generate inputs using threads because the change
relates to free-threaded builds. It's ok to generate inputs that excercise the affected code,
but without using threading.

 If PR is directly related to any of the above critical aviod areas, and there is no 
 safe alternative codepath to explore, then skip the PR and return an empty list of inputs.

 The fuzzer has agressive timeouts, so avoid inputs that may take a long time to run, or that will cause the interpreter to hang or enter an infinite loop.

Here is the PR description:
{pr_description}

Each test will be evaluated as python code.
Output the inputs sequentially, starting with <|start|>, separated by <|next|> and ending with <|end|>, like this:
<|start|>
(input 1)
<|next|>
(input 2)
<|next|>
...
(input 5)
<|end|>
"""

    result = await client.responses.parse(
        model='gpt-5.4',
        input=prompt,
    )
    text = result.output_text.strip()
    text = text.split("<|start|>")[1].split("<|end|>")[0].strip()
    input_texts = [part.strip() for part in text.split("<|next|>")]

    test_dir = TESTS_ROOT / f"{pr_number}"
    test_dir.mkdir(exist_ok=True)
    for i, input_text in enumerate(input_texts):
        input_path = test_dir / f"input_{i+1}.txt"
        input_path.write_text(input_text)


async def generate_test_cases(pr_numbers: list[int]):
    tasks = [generate_test_cases_one(pr_number) for pr_number in pr_numbers]
    _ = await tqdm.gather(*tasks, desc="Generating test cases")
