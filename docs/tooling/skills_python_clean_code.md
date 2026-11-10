# AI Coding Skills: Python Clean Code Principles

This document outlines the core coding skills and standards required for generating clean, maintainable, and efficient Python code. These principles ensure that code is readable by humans, scalable for teams, and free of technical debt.

Target: Python 3.10+ (matching `requires-python` in `customer360-dao/pyproject.toml`). Code snippets are illustrative; `pass` and `...` are placeholders.

## Core Philosophy
1. **Readability:** Immediate team comprehension.
2. **Maintainability:** Effortless future modifications.
3. **Efficiency:** Lower long-term cost by avoiding technical debt.
4. **Simplicity:** Solve the current requirement; do not build for hypothetical futures (KISS, YAGNI).
5. **Testability:** Small, explicit, dependency-injected units are easy to verify.

---

## Section 1: Naming Conventions

### 1. Casing
Use `snake_case` for variables and functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Avoid `camelCase` in Python code.
```python
# ❌ Avoid (camelCase)
firstName = "John"
isEmployed = True

# ✅ Preferred (snake_case)
first_name = "John"
is_employed = True
```

### 2. Exhibit Intent
Variables must explain their exact purpose instantly without requiring comments.
```python
# ❌ Avoid (Blind variables)
i = 0
loc = "New York"

# ✅ Preferred (Explicit meaning)
user_count = 0
destination = "New York"
```

### 3. Avoid Confusing Names
Vague terminology forces developers to guess the data structure.
```python
# ❌ Avoid (Vague)
temp = x + y
userf = "John"

# ✅ Preferred (Precise)
total_sum = x + y
user_first_name = "John"
```

### 4. No Abbreviations
Never shorten words. Storage is cheap, confusion is expensive. Well-established terms such as `id`, `url`, and `api` are acceptable.
```python
# ❌ Avoid 
usr = "John Doe"
DoB = "1990-01-01"

# ✅ Preferred 
user_name = "John Doe"
date_of_birth = "1990-01-01"
```

### 5. Class Naming
Classes represent tangible entities. Use `PascalCase` nouns, not single letters or verbs.
```python
# ❌ Avoid (Single Letter / Action)
class R: pass
class Calculate: pass

# ✅ Preferred (PascalCase Nouns)
class Rectangle: pass
class UserAccount: pass
```

### 6. Method Naming
Functions and methods perform specific tasks. They must be action verbs.
```python
# ❌ Avoid (Noun as a method)
def object_location(entity): pass

# ✅ Preferred (Action verb)
def calculate_location(entity): pass
```

### 7. Consistent Vocabulary
Pick one word for a concept and stick with it across all classes.
```python
# ❌ Avoid (Synonyms)
def calculate_salary(): pass
def compute_pay(): pass

# ✅ Preferred (Strict Uniformity)
def calculate_salary(): pass
# Use 'calculate_salary' everywhere.
```

---

## Section 2: PEP 8 Standards

### 8. Indentation
Strictly use 4 spaces per indentation level. Never mix tabs and spaces.
```python
# ❌ Avoid (Irregular)
if True:
  print("2 spaces")

# ✅ Preferred (4 spaces)
if True:
    print("4 spaces")
```

### 9. Line Length Cap
Limit code lines to 79 characters and comments/docstrings to 72 (PEP 8). If the project configures a different limit (for example in `pyproject.toml`), follow it. Break long expressions cleanly.
```python
# ❌ Avoid (Horizontal Scroll)
long_string = "This is a very long variable name that exceeds the 79 char limit"

# ✅ Preferred (Line Breaks)
long_string = (
    "This is a very long variable "
    "that fits within limits"
)
```

### 10. Operator Whitespace
Provide breathing room around mathematical and assignment operators.
```python
# ❌ Avoid 
total=price+tax

# ✅ Preferred 
total = price + tax
```

Keyword arguments and default values are the exception: no spaces around `=`.
```python
# ❌ Avoid
send(message, retries = 3)

# ✅ Preferred
send(message, retries=3)
```

---

## Section 3: Effective Comments

### 11. Avoid Redundancy
Do not state the obvious. Trust the code to explain *what* it is doing.
```python
# ❌ Avoid 
# Loop through names
for name in names:
    # Print the name
    print(name)

# ✅ Preferred 
for name in names:
    print(name)
```

### 12. Explain the "Why", Not the "What"
Comments should explain business logic, context, or edge-case reasoning.
```python
# ❌ Avoid (Describing the 'What')
# Check if string is empty
if len(input_data) == 0: pass

# ✅ Preferred (Describing the Context)
# Prevent API rejection state
if len(input_data) == 0: pass
```

### 13. Docstrings
Document every public module, class, and function. Start with a one-line summary in the imperative mood (PEP 257), then describe `Args` and `Returns` (Google style, as used in the repository).
```python
# ❌ Avoid (No documentation)
def sum_list(numbers):
    ...

# ✅ Preferred
def sum_list(numbers: list[int]) -> int:
    """Calculate the sum of a list of integers.

    Args:
        numbers: Integers to add together.

    Returns:
        The total of all values in ``numbers``.
    """
    return sum(numbers)
```

### 14. Type Hints
Annotate public function signatures and class attributes. Type checkers such as mypy or pyright catch errors before execution and improve IDE autocompletion; Python does not enforce hints at runtime. On Python 3.10+, prefer built-in generics (`list[int]`) and unions (`int | None`) over `typing.List` and `Optional`.
```python
# ❌ Avoid (No types, unclear contract)
def sum_list(numbers):
    return sum(numbers)

# ✅ Preferred (Explicit types)
def sum_list(numbers: list[int]) -> int:
    return sum(numbers)
```

---

## Section 4: Writing Functions

### 15. Clear Function Names
Function names should clearly state their output or action.
```python
# ❌ Avoid (Cryptic)
def calc_avg(num_list): pass

# ✅ Preferred (Descriptive)
def calculate_average(numbers): pass
```

### 16. Limit Parameters
Functions with many parameters are hard to call and test. Group related values into a typed object (see Section 11) rather than a loose dict or `**kwargs`, which hide the expected fields.
```python
from dataclasses import dataclass

# ❌ Avoid (Overloaded, easy to pass arguments in the wrong order)
def create_user(name, email, phone, city, state): ...

# ✅ Preferred (Grouped data)
@dataclass(frozen=True)
class ContactDetails:
    email: str
    phone: str
    city: str
    state: str

def create_user(name: str, contact: ContactDetails) -> None: ...
```

### 17. Keep Functions Short
One function equals one clear responsibility. Never mix multiple tasks.
```python
# ❌ Avoid (Does two things)
def fetch_and_parse_data(): pass

# ✅ Preferred (Separated concerns)
def fetch_data(): pass
def parse_data(raw_data): pass
```

### 18. Avoid Global Variables
Global state can be changed from anywhere, which makes behavior hard to predict and test. Pass state in explicitly and return new values.
```python
# ❌ Avoid (Mutation Risk)
x = 0
def increment():
    global x
    x += 1

# ✅ Preferred (State passed explicitly)
def increment(value: int) -> int:
    return value + 1

def run() -> None:
    counter = 0
    counter = increment(counter)
```

---

## Section 5: Writing Classes

### 19. Single Responsibility Principle (SRP)
A class should have only one reason to change.
```python
# ❌ Avoid (Multiple reasons to change)
class Invoice:
    def calculate_total(self): ...
    def print_invoice(self): ...
    def save_to_database(self): ...

# ✅ Preferred (Focused tightly on one concept)
class Car:
    def __init__(self, make):
        self.make = make
        self.speed = 0
        
    def start(self): pass
    def stop(self): pass
```

### 20. Limit Methods
God-classes are unmaintainable. Extract distinct behaviors into separate classes.
```python
# ❌ Avoid (God-Class)
class User:
    def create_account(self): pass
    def send_email(self): pass # Should be EmailService
    def generate_pdf(self): pass # Should be ReportService
```

### 21. Use Inheritance
Extract shared logic to parent classes to avoid duplicating core structures. Use inheritance only for true "is-a" relationships; for "has-a" or shared behavior, prefer composition (see rule 38).
```python
# ❌ Avoid (Duplicated Base)
class Circle:
    def __init__(self, x, y):
        self.x = x
        self.y = y

# ✅ Preferred (Shared Parent)
class Shape:
    def __init__(self, x, y):
        self.x = x
        self.y = y

class Circle(Shape):
    pass
```

---

## Section 6: Don't Repeat Yourself (DRY)

### 22. Extract Common Code
Identify repeated code blocks and extract them into reusable callers.
```python
# ❌ Avoid (Repeated Loop)
def calc_sum(nums):
    t = 0
    for n in nums: t += n
    return t

def calc_avg(nums):
    t = 0
    for n in nums: t += n
    return t / len(nums)

# ✅ Preferred (Reusable Caller)
def calc_sum(nums):
    return sum(nums)

def calc_avg(nums):
    return sum(nums) / len(nums)
```

### 23. Polymorphism Over Type Checks
Define the shared interface in a base class and implement the details in each subclass. Callers then work with the interface and never need `isinstance` checks.
```python
import math
from abc import ABC, abstractmethod

# ❌ Avoid (Type checks spread across callers)
def describe(shape):
    if isinstance(shape, Circle):
        return "circle"

# ✅ Preferred (Abstract interface)
class Shape(ABC):
    @abstractmethod
    def area(self) -> float:
        """Return the area of the shape."""

class Circle(Shape):
    def __init__(self, radius: float) -> None:
        self.radius = radius

    def area(self) -> float:
        return math.pi * self.radius ** 2
```

---

## Section 7: Handling Errors

### 24. The try-except Block
Catch explicit errors. Never use bare `except:`, and avoid catching `Exception` unless you re-raise or log it at a boundary. When translating an error, chain it with `raise ... from exc` so the original cause is kept.
```python
# ❌ Avoid (Bare except hides every error, including KeyboardInterrupt)
try:
    y = 1 / x
except:
    print("Something went wrong.")

# ✅ Preferred
try:
    y = 1 / x
except ZeroDivisionError:
    print("You cannot divide by zero.")
except ValueError:
    print("Invalid number format.")
```

### 25. The finally Block
Always executes. Use it for resource cleanup, and only for resources that were actually acquired. Prefer a context manager (see rule 39) when one is available.
```python
# ❌ Avoid (db is unbound in finally if connect() fails)
try:
    db = connect()
    process_data(db)
finally:
    db.close()

# ✅ Preferred (Acquire outside try, release in finally)
db = connect()  # Raises ConnectionError before anything needs closing
try:
    process_data(db)
finally:
    db.close()  # Always runs, even if process_data raises
```

### 26. Raising Exceptions
Don't fail silently. Raise explicit exceptions when parameters are invalid.
```python
# ❌ Avoid (Silent Failure)
def divide(x, y):
    if y == 0: return None 
    return x / y

# ✅ Preferred (Explicit Raise)
def divide(x, y):
    if y == 0:
        raise ValueError("Cannot divide by zero")
    return x / y
```

### 27. Assertions
Use `assert` only for internal invariants. Assertions are removed when Python runs with `-O`, so validate public inputs with explicit exceptions (see rule 26 and rule 40).
```python
DISCOUNT_RATE = 0.9

# ❌ Avoid (Public input validated by assert)
def apply_discount(price):
    assert price > 0, "Price must be positive"
    return price * DISCOUNT_RATE

# ✅ Preferred (Explicit input check, internal invariant as assert)
def apply_discount(price: float) -> float:
    if price <= 0:
        raise ValueError("Price must be positive")
    discounted_price = price * DISCOUNT_RATE
    assert discounted_price <= price, "Discount must not increase price"
    return discounted_price
```

---

## Section 8: Testing Code

### 28. Unit Testing
Every new feature needs unit tests. Test observable behavior, cover edge cases and error paths, and keep each test focused on one behavior. The repository uses pytest (see the `pytest.ini` files under `customer360-backend/`).
```python
import pytest

# ✅ Preferred (Parametrized edge cases)
def add(a: int, b: int) -> int:
    return a + b

@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(2, 3, 5), (-1, 1, 0), (0, 0, 0)],
)
def test_add_returns_sum(a: int, b: int, expected: int) -> None:
    assert add(a, b) == expected
```

---

## Section 9: Refactoring

### 29. Extract Method
Break large blocks of logic into smaller, delegated methods.
```python
# ❌ Avoid (Block Logic)
def print_avg(a, b, c):
    total = a + b + c
    avg = total / 3
    print(avg)

# ✅ Preferred (Delegated Logic)
def print_avg(a, b, c):
    avg = calc_total(a, b, c) / 3
    print(avg)

def calc_total(a, b, c):
    return a + b + c
```

### 30. Replace Magic Numbers
Do not hard-code arbitrary numbers. Use named constants.
```python
# ❌ Avoid (Hard-coded)
def interest(p, r, t):
    # What is 100?
    return (p * r * t) / 100

# ✅ Preferred (Symbolic Constants)
RATE_DIVISOR = 100

def interest(p, r, t):
    return (p * r * t) / RATE_DIVISOR
```

---

## Section 10: Pythonic Idioms

### 31. Guard Clauses and Early Returns
Handle invalid or edge cases first and return early, so the main path stays flat and easy to follow.
```python
# ❌ Avoid (Nested)
def ship_order(order):
    if order is not None:
        if order.items:
            if order.is_paid:
                return dispatch(order)
    return None

# ✅ Preferred (Early returns)
def ship_order(order):
    if order is None or not order.items:
        return None
    if not order.is_paid:
        return None
    return dispatch(order)
```

### 32. Comprehensions and Built-ins
Prefer comprehensions and built-ins such as `sum`, `any`, `all`, `enumerate`, and `zip` over manual accumulation. Use a plain loop when a comprehension needs multiple conditions or nested loops.
```python
# ❌ Avoid (Manual accumulation)
active_names = []
for user in users:
    if user.is_active:
        active_names.append(user.name)

# ✅ Preferred
active_names = [user.name for user in users if user.is_active]
```

### 33. Use f-strings
Format strings with f-strings instead of concatenation or `str()` calls.
```python
# ❌ Avoid
message = "Hello, " + user_name + "! You have " + str(count) + " messages."

# ✅ Preferred
message = f"Hello, {user_name}! You have {count} messages."
```

### 34. Avoid Mutable Default Arguments
Default values are evaluated once, at definition time, so a mutable default is shared across all calls.
```python
# ❌ Avoid (The same list is reused by every call)
def add_tag(tag, tags=[]):
    tags.append(tag)
    return tags

# ✅ Preferred (None sentinel)
def add_tag(tag: str, tags: list[str] | None = None) -> list[str]:
    if tags is None:
        tags = []
    tags.append(tag)
    return tags
```

### 35. Idiomatic Comparisons
Use `is None` for `None` checks, rely on truthiness for empty containers, and never compare booleans with `==`.
```python
# ❌ Avoid
if value == None: ...
if len(items) == 0: ...
if is_ready == True: ...

# ✅ Preferred
if value is None: ...
if not items: ...
if is_ready: ...
```

### 36. Replace Magic Strings with Enums
Use an `Enum` for a fixed set of values so typos fail loudly and IDEs can autocomplete.
```python
from enum import Enum

# ❌ Avoid (Typos go unnoticed)
def mark_shipped(order):
    order.status = "shiped"

# ✅ Preferred
class OrderStatus(str, Enum):
    PENDING = "pending"
    SHIPPED = "shipped"

def mark_shipped(order) -> None:
    order.status = OrderStatus.SHIPPED
```

---

## Section 11: Data Modeling and Design

### 37. Use Dataclasses for Data Containers
Use `@dataclass` for structured data instead of hand-written `__init__` methods or untyped dicts. Use `frozen=True` when instances should not change.
```python
from dataclasses import dataclass

# ❌ Avoid (Boilerplate and untyped dict)
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

point = {"x": 1, "y": 2}

# ✅ Preferred
@dataclass(frozen=True)
class Point:
    x: float
    y: float
```

### 38. Prefer Composition Over Inheritance
Inherit only for true "is-a" relationships. Inject collaborators for behavior reuse. This also makes them easy to replace in tests.
```python
from typing import Protocol

# ❌ Avoid (Inheriting behavior just to reuse it)
class Logger:
    def send(self, message: str) -> None: ...

class UserService(Logger):
    def register(self, email: str) -> None:
        self.send(f"Welcome, {email}")

# ✅ Preferred (Injected collaborator)
class Notifier(Protocol):
    def send(self, message: str) -> None: ...

class UserService:
    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    def register(self, email: str) -> None:
        self._notifier.send(f"Welcome, {email}")
```

### 39. Use Context Managers for Resources
Use `with` for files, connections, locks, and other resources that need guaranteed cleanup.
```python
from pathlib import Path

# ❌ Avoid (Easy to forget to close)
handle = open("report.csv")
data = handle.read()
handle.close()

# ✅ Preferred
data = Path("report.csv").read_text(encoding="utf-8")

with open("report.csv", encoding="utf-8") as handle:
    data = handle.read()
```

### 40. Validate Public Inputs at the Boundary
Validate arguments of public functions and API handlers before use. Reject invalid data early with an explicit exception instead of letting it flow deeper.
```python
# ❌ Avoid (Invalid data reaches the model)
def create_segment(name, max_size):
    return Segment(name=name, max_size=max_size)

# ✅ Preferred
def create_segment(name: str, max_size: int) -> Segment:
    if not name.strip():
        raise ValueError("Segment name must not be empty")
    if max_size <= 0:
        raise ValueError("max_size must be a positive integer")
    return Segment(name=name, max_size=max_size)
```

### 41. Do Not Over-Engineer (KISS, YAGNI)
Build what the current requirement needs. Do not add abstractions, parameters, or configuration for hypothetical future use.
```python
# ❌ Avoid (Speculative abstraction for one caller)
class AbstractEmailStrategyFactory: ...

# ✅ Preferred
def send_welcome_email(address: str) -> None: ...
```

---

## Section 12: Modules, Imports, and Logging

### 42. Import Order and Style
Group imports as standard library, third-party, then local, separated by blank lines. Use one import per line, absolute imports, and avoid wildcard imports.
```python
# ❌ Avoid
from os import *
import json, sys
from utils import *

# ✅ Preferred
import json
import sys
from pathlib import Path

import requests

from myproject.services import segment_service
```

### 43. Use Logging, Not print
Use the `logging` module for diagnostics. Use lazy `%s` arguments so formatting only happens when the message is emitted. Never log secrets or personal data.
```python
import logging

logger = logging.getLogger(__name__)

# ❌ Avoid
print("Sync started")

# ✅ Preferred
logger.info("Sync started for tenant %s", tenant_id)
```

### 44. Guard the Entry Point
Put script execution behind `if __name__ == "__main__":` so importing the module has no side effects.
```python
# ❌ Avoid (Runs on import)
main()

# ✅ Preferred
def main() -> None:
    ...

if __name__ == "__main__":
    main()
```

### 45. Comments: No Dead Code, Linked TODOs
Delete commented-out code; version control keeps history. Attach TODOs to an owner or ticket so they can be tracked.
```python
# ❌ Avoid
# result = old_calculation(values)
# TODO fix this later

# ✅ Preferred
# TODO(owner, TICKET-123): Replace with a batch lookup once the index exists.
```

---

## Section 13: Tooling and Review Checklist

Run the project's configured tools before merging (check `pyproject.toml`, `pytest.ini`, and the CI workflow for exact commands). Typical tools are a linter/formatter such as ruff, a type checker such as mypy or pyright, and pytest.

Before submitting Python code, verify:
- [ ] Names describe intent; no unnecessary abbreviations.
- [ ] Each function and class has one clear responsibility.
- [ ] Public functions and classes have type hints and docstrings.
- [ ] Errors are explicit: no bare `except`, no silent `return None` on failure.
- [ ] No mutable default arguments, global state, magic numbers, or commented-out code.
- [ ] Public inputs are validated; invalid input fails with a clear exception.
- [ ] New behavior has unit tests, including edge and error cases.
- [ ] Logs contain no secrets or personal data.