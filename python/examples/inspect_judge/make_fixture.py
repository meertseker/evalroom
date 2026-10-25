"""Builds ``judge_fixture.eval``: a real Inspect log with a judge that is sometimes wrong and
60 human corrections recorded with ``edit_score``.

- 80 short factual questions; the model under test answers them (some answers are wrong,
  some are right but phrased differently, a few are only half right).
- ``model_graded_qa`` grades them with a mock judge that makes realistic mistakes: it rejects
  some correct paraphrases, accepts some plausible wrong answers and gives full credit to
  half-right answers. 12 of 80 grades (15%) are wrong.
- Reviewers then check 60 samples, chosen with a fixed seed, and record each verdict with
  ``edit_score`` and ``ProvenanceData``: a confirmation when the judge was right, a
  correction with a reason when it was not.

No API key is needed: both models are ``mockllm/model`` with deterministic outputs keyed by
question, so the grades and labels are identical on every run (ids and timestamps inside the
log are not).

    uv run python examples/inspect_judge/make_fixture.py
"""

from __future__ import annotations

import random
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.log import ProvenanceData, edit_score, read_eval_log, write_eval_log
from inspect_ai.model import ChatMessage, GenerateConfig, ModelOutput, get_model
from inspect_ai.scorer import ScoreEdit, accuracy, model_graded_qa, stderr
from inspect_ai.solver import generate
from inspect_ai.tool import ToolChoice, ToolInfo
from inspect_ai.util import registry_create

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "judge_fixture.eval"
SEED = 2026
REVIEWED = 60

Grade = Literal["C", "I", "P"]


@dataclass(frozen=True)
class Question:
    question: str
    target: str
    answer: str
    truth: Grade
    """What a careful human says about the answer."""
    judge: Grade
    """What the mock judge says."""
    why: str = ""
    """The reviewer's reason when the judge is wrong."""


def ok(question: str, target: str, answer: str | None = None) -> Question:
    return Question(question, target, answer or target, "C", "C")


def wrong(question: str, target: str, answer: str) -> Question:
    return Question(question, target, answer, "I", "I")


QUESTIONS: list[Question] = [
    ok("What is the capital of France?", "Paris"),
    ok("How many legs does a spider have?", "8"),
    ok("What is the chemical symbol for gold?", "Au"),
    ok("Which planet is known as the red planet?", "Mars"),
    ok("What is the largest ocean on Earth?", "The Pacific Ocean", "Pacific"),
    ok("Who wrote 'Pride and Prejudice'?", "Jane Austen"),
    ok("What is the boiling point of water at sea level in Celsius?", "100", "100 °C"),
    ok("How many continents are there?", "7", "Seven"),
    ok("What gas do plants absorb from the air for photosynthesis?", "Carbon dioxide", "CO2"),
    ok("What is the square root of 81?", "9"),
    ok("In which country are the pyramids of Giza?", "Egypt"),
    ok("What is the hardest natural substance?", "Diamond"),
    ok("What is the smallest prime number?", "2"),
    ok("Which organ pumps blood around the body?", "The heart", "Heart"),
    ok("What is the capital of Japan?", "Tokyo"),
    ok("How many minutes are in an hour?", "60"),
    ok("Who painted the Mona Lisa?", "Leonardo da Vinci"),
    ok("What is the freezing point of water in Fahrenheit?", "32"),
    ok("What is the longest river in South America?", "The Amazon", "Amazon River"),
    ok("Which element has the atomic number 1?", "Hydrogen"),
    ok("What is the capital of Canada?", "Ottawa"),
    ok("How many sides does a hexagon have?", "6", "Six"),
    ok("What language has the most native speakers?", "Mandarin Chinese", "Mandarin"),
    ok("What is the largest planet in the solar system?", "Jupiter"),
    ok("Who developed the theory of general relativity?", "Albert Einstein", "Einstein"),
    ok("What is 12 multiplied by 12?", "144"),
    ok("What is the main ingredient of guacamole?", "Avocado"),
    ok("Which metal is liquid at room temperature?", "Mercury"),
    ok("What is the capital of Italy?", "Rome"),
    ok("How many bones are in the adult human body?", "206"),
    ok("What is the currency of the United Kingdom?", "The pound sterling", "Pound sterling"),
    ok("Which instrument has 88 keys?", "The piano", "Piano"),
    ok("What is the tallest mountain above sea level?", "Mount Everest", "Everest"),
    ok("In what year did the first person walk on the Moon?", "1969"),
    ok("What is the largest mammal?", "The blue whale", "Blue whale"),
    ok("What is the capital of Spain?", "Madrid"),
    ok("What is H2O commonly called?", "Water"),
    ok("Which continent is Kenya in?", "Africa"),
    ok("How many hours are in a day?", "24"),
    ok("What colour do you get by mixing blue and yellow?", "Green"),
    ok("Who was the first President of the United States?", "George Washington"),
    ok("What is the capital of Germany?", "Berlin"),
    ok("Which planet has the most prominent rings?", "Saturn"),
    ok(
        "What is the speed of light in a vacuum, roughly, in km/s?", "300,000", "About 300,000 km/s"
    ),
    ok("What is the plural of 'mouse'?", "Mice"),
    ok("What shape has three sides?", "A triangle", "Triangle"),
    ok("Which country is home to the kangaroo?", "Australia"),
    ok("What is the capital of Egypt?", "Cairo"),
    ok("How many players are on a football (soccer) team on the field?", "11", "Eleven"),
    ok("What is the opposite of 'north'?", "South"),
    ok("What is 15 divided by 3?", "5"),
    ok("Which gas makes up most of Earth's atmosphere?", "Nitrogen"),
    ok("What is the capital of Portugal?", "Lisbon"),
    ok("How many weeks are in a year?", "52"),
    # Correct, but phrased in a way the judge sometimes refuses.
    Question(
        "What is the capital of Australia?",
        "Canberra",
        "It's Canberra, not Sydney.",
        "C",
        "I",
        "Answer says Canberra; the extra clause is not a different answer.",
    ),
    Question(
        "How many days are in a leap year?",
        "366",
        "Three hundred sixty-six",
        "C",
        "I",
        "366 written out in words is still 366.",
    ),
    Question(
        "Who wrote 'Romeo and Juliet'?",
        "William Shakespeare",
        "Shakespeare (William)",
        "C",
        "I",
        "Same person; name order does not matter.",
    ),
    Question(
        "What is the chemical formula for table salt?",
        "NaCl",
        "NaCl (sodium chloride)",
        "C",
        "I",
        "Formula is exactly right; the gloss is harmless.",
    ),
    # Wrong answers the judge correctly rejects.
    wrong("What is the capital of Brazil?", "Brasília", "Rio de Janeiro"),
    wrong("How many planets are in the solar system?", "8", "9"),
    wrong("What is the largest desert in the world?", "Antarctica", "The Gobi"),
    wrong("Who discovered penicillin?", "Alexander Fleming", "Louis Pasteur"),
    wrong("What is the capital of Turkey?", "Ankara", "Istanbul"),
    wrong("What is the smallest country in the world?", "Vatican City", "Monaco"),
    wrong("Which element has the symbol K?", "Potassium", "Krypton"),
    wrong("In which year did World War II end?", "1945", "1944"),
    # Plausible wrong answers the judge wrongly accepts.
    Question(
        "What is the capital of Switzerland?",
        "Bern",
        "Zurich",
        "I",
        "C",
        "Zurich is the largest city; the capital is Bern.",
    ),
    Question(
        "What is the chemical symbol for silver?", "Ag", "Au", "I", "C", "Au is gold. Silver is Ag."
    ),
    Question(
        "How many hearts does an octopus have?", "3", "2", "I", "C", "Octopuses have three hearts."
    ),
    Question(
        "What is the longest river in Africa?",
        "The Nile",
        "The Congo",
        "I",
        "C",
        "The Nile, not the Congo.",
    ),
    Question(
        "Who invented the telephone?",
        "Alexander Graham Bell",
        "Thomas Edison",
        "I",
        "C",
        "Edison did not; the patent is Bell's.",
    ),
    Question(
        "What is the capital of New Zealand?",
        "Wellington",
        "Auckland",
        "I",
        "C",
        "Auckland is the biggest city; the capital is Wellington.",
    ),
    # Half-right answers.
    Question(
        "Name the two largest planets in the solar system.",
        "Jupiter and Saturn",
        "Jupiter and Neptune",
        "P",
        "C",
        "Only Jupiter is right; Saturn is second, not Neptune.",
    ),
    Question(
        "What are the primary colours of light?",
        "Red, green and blue",
        "Red and blue",
        "P",
        "C",
        "Missing green: half right at best.",
    ),
    Question(
        "Which two elements make up water?",
        "Hydrogen and oxygen",
        "Hydrogen and nitrogen",
        "P",
        "I",
    ),
    Question(
        "Name the first two people to walk on the Moon.",
        "Neil Armstrong and Buzz Aldrin",
        "Neil Armstrong and Michael Collins",
        "P",
        "I",
    ),
    # More wrong answers, correctly rejected.
    wrong("What is the square root of 144?", "12", "14"),
    wrong("What is the capital of Canada's province of Ontario?", "Toronto", "Ottawa"),
    wrong("Which blood type is the universal donor?", "O negative", "AB positive"),
    wrong("How many strings does a standard violin have?", "4", "6"),
]

assert len(QUESTIONS) == 80, len(QUESTIONS)
# Judge errors under the default partial="fail" policy (half right counts as incorrect).
assert sum(q.judge != ("I" if q.truth == "P" else q.truth) for q in QUESTIONS) == 12

BY_QUESTION = {q.question: q for q in QUESTIONS}
CONFIRM = [
    "Checked against the reference answer.",
    "Agree with the grade.",
    "Matches the target.",
    "Grade is right.",
    "Looked it up; the grade stands.",
]
REVIEWERS = ["ana", "ben", "chen"]
TASK_RE = re.compile(r"\[Task\]: (.*?)\n", re.S)


def solver_output(
    messages: list[ChatMessage],
    tools: list[ToolInfo],
    tool_choice: ToolChoice,
    config: GenerateConfig,
) -> ModelOutput:
    question = BY_QUESTION[messages[-1].text.strip()]
    return ModelOutput.from_content("mockllm/model", question.answer)


def judge_output(
    messages: list[ChatMessage],
    tools: list[ToolInfo],
    tool_choice: ToolChoice,
    config: GenerateConfig,
) -> ModelOutput:
    match = TASK_RE.search(messages[-1].text)
    question = BY_QUESTION[match.group(1).strip()] if match else None
    if question is None:
        return ModelOutput.from_content("mockllm/model", "GRADE: I")
    if question.judge == "C":
        reasoning = f"The submission '{question.answer}' matches the criterion '{question.target}'."
    else:
        reasoning = f"The submission '{question.answer}' does not state '{question.target}'."
    return ModelOutput.from_content("mockllm/model", f"{reasoning}\n\nGRADE: {question.judge}")


def build() -> Path:
    task = Task(
        name="inspect_judge",
        dataset=[
            Sample(input=q.question, target=q.target, id=f"q{i + 1:02d}")
            for i, q in enumerate(QUESTIONS)
        ],
        solver=generate(),
        scorer=model_graded_qa(model=get_model("mockllm/model", custom_outputs=judge_output)),
        # Looked up by name: proves Inspect loads evalroom's entry point.
        metrics=[accuracy(), stderr(), registry_create("metric", "evalroom/wilson_accuracy")],
    )
    with tempfile.TemporaryDirectory() as scratch:
        [log] = eval(
            task,
            model=get_model("mockllm/model", custom_outputs=solver_output),
            log_dir=scratch,
            display="none",
        )
        # Read it back fully while the scratch directory still exists.
        log = read_eval_log(log.location)
    assert log.samples is not None
    rng = random.Random(SEED)
    reviewed = sorted(rng.sample(range(len(QUESTIONS)), REVIEWED))
    for index in reviewed:
        question = QUESTIONS[index]
        sample_id = f"q{index + 1:02d}"
        reviewer = rng.choice(REVIEWERS)
        if question.truth == question.judge:
            reason = rng.choice(CONFIRM)
        else:
            reason = question.why or f"Partly right: the target is {question.target}."
        edit_score(
            log,
            sample_id,
            "model_graded_qa",
            ScoreEdit(
                value=question.truth,
                provenance=ProvenanceData(author=reviewer, reason=reason),
            ),
        )
    write_eval_log(log, str(FIXTURE))
    return FIXTURE


if __name__ == "__main__":
    path = build()
    print(f"wrote {path.relative_to(HERE.parent.parent)} ({path.stat().st_size // 1024} KiB)")
