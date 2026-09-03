"""Recovering code from an iteration log must not depend on the API."""

from __future__ import annotations

import pytest

from fuzzer.extract import raw_response
from fuzzer.strategy_io import ExtractionError, extract_code

LOG = """# Iteration 1 (seed)

## Prompt

```
Write a strategy. Here is an example: ```python\\nnot_the_answer = 1\\n```
```

## Raw response

Sure, here it is.

```python
from hypothesis import strategies as st
STRATEGY = st.just("<a/>")
```
"""


def test_takes_the_reply_not_the_prompt():
    """The prompt section can itself contain fenced code.

    Extracting from the whole log would find the prompt's example first and
    silently produce the wrong module.
    """
    code = extract_code(raw_response(LOG))
    assert "STRATEGY" in code
    assert "not_the_answer" not in code


def test_plain_file_without_headings_is_treated_as_a_reply():
    """So a reply pasted into a file by hand also works."""
    code = extract_code(raw_response("```python\nSTRATEGY = 1\n```"))
    assert code == "STRATEGY = 1"


def test_refuses_a_reply_with_no_code():
    with pytest.raises(ExtractionError):
        extract_code(raw_response("## Raw response\n\nI cannot help with that."))


if __name__ == "__main__":
    import sys

    import pytest as _pytest

    sys.exit(_pytest.main([__file__, "-q"]))
